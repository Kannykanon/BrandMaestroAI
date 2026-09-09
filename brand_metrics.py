import hashlib
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from threading import Lock

import redis
from sqlalchemy.exc import IntegrityError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from database import BrandMetrics, get_db_session, BrandBrain
from model import LLMSingleton
from prompts.metrics import METRICS_EXTRACTION, METRICS_SYNTHESIS, METRICS_SYNTHESIS_SINGLE, METRICS_SYNTHESIS_TEMPLATE

logger = logging.getLogger(__name__)

from datetime import datetime

# Redis TTL for cached brand context — 24 hours
CACHE_TTL = 86400

# How long a superseded brand context stays servable after invalidation.
# Re-synthesis is debounced (see celery_task.extract_metrics), so without this
# every generation landing in the debounce window paid for a full synchronous
# LLM synthesis. Serving the previous brain for a short grace period trades a
# few seconds of staleness for removing that latency spike entirely.
STALE_TTL = 900

# Maximum number of recent per-document profiles passed in full to the
# synthesis LLM. Older rows are already baked into the previous synthesis
# and don't need to be re-sent verbatim.
MAX_RECENT_PROFILES = 10

# The asset_bank fields the extraction prompt requires to be copied VERBATIM
# out of the source document. Everything the brain presents to the writer as a
# "PERMITTED BRAND CLAIM" comes from these, so they are the only fields that
# carry facts rather than voice.
_FACT_BEARING_FIELDS = (
    "social_proof_claims",
    "named_frameworks",
    "stated_values",
    "financial_targets",
)

# Placeholder strings the extraction LLM emits when a field has no content.
_EMPTY_CLAIM_VALUES = frozenset({
    "", "none", "n/a", "na", "not extracted", "not applicable", "unknown",
})

_QUOTE_TRANSLATION = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
})


def _normalise_for_grounding(text: str) -> str:
    """
    Fold the differences that survive a faithful copy — curly quotes, dash
    variants, non-breaking spaces, line wrapping — so grounding compares
    wording rather than typography. Anything beyond that stays significant:
    a claim whose numbers or nouns differ from the document is not grounded.
    """
    return " ".join(str(text).translate(_QUOTE_TRANSLATION).split()).lower()


def _ground_asset_bank(extracted: dict, source_text: str) -> tuple[dict, list[str]]:
    """
    Drop asset_bank claims that do not actually appear in the document they
    were extracted from.

    The extraction prompt instructs the LLM to copy these claims VERBATIM, and
    the brain then hands them to the writer as the closed list of facts it is
    permitted to state. That makes the VERBATIM instruction a load-bearing
    guarantee, and an instruction is not a guarantee — so it is checked here
    instead of trusted. Returns the filtered profile and the dropped claims.
    """
    bank = extracted.get("asset_bank")
    if not isinstance(bank, dict):
        return extracted, []

    haystack = _normalise_for_grounding(source_text)
    dropped: list[str] = []
    cleaned_bank = dict(bank)

    for field in _FACT_BEARING_FIELDS:
        claims = bank.get(field)
        if isinstance(claims, str):
            claims = [claims]
        if not isinstance(claims, list):
            continue

        kept = []
        for claim in claims:
            normalised = _normalise_for_grounding(claim)
            if normalised in _EMPTY_CLAIM_VALUES:
                continue
            if normalised in haystack:
                kept.append(claim)
            else:
                dropped.append(f"{field}: {claim}")
        cleaned_bank[field] = kept

    result = dict(extracted)
    result["asset_bank"] = cleaned_bank
    return result, dropped


class MetricPort(ABC):
    @property
    @abstractmethod
    def source(self) -> str:
        """Returns the storage backend — 'postgres', 'mongodb', etc."""

    @abstractmethod
    def extract_and_save(self, doc_id: int, doc_content: str) -> bool:
        """
        Extract metrics from a single document and persist as a new
        BrandMetrics row. Returns True if a new row was inserted,
        False if this document was already processed (idempotent).
        """
        ...

    @abstractmethod
    def build_and_cache_context(self) -> str:
        """
        Fetch all metric rows for this (business_id, content_type),
        synthesize them via LLM, and write the result to Redis.
        Returns the synthesized context string.
        """
        ...

    @abstractmethod
    def get_context(self) -> str:
        """
        Return the brand context string for use by the writer/enforcer.
        Reads from Redis cache; falls back to build_and_cache_context()
        if the cache is cold.
        """
        ...


def _syllables(word: str) -> int:
    """Rough syllable count: vowel groups, minimum one.

    Good enough to separate "nap" from "gratification", which is all it is
    for. A real dictionary lookup would cost a dependency for no gain here.
    """
    return max(1, len(re.findall(r"[aeiouy]+", word.lower())))


_redis_pool = None
_redis_pool_lock = Lock()


def _get_redis_client() -> "redis.Redis":
    """Shared Redis client for every BrandMetricsSQL instance.

    graph/deps.resolve_deps() constructs a fresh BrandMetricsSQL for each node
    invocation, and redis.Redis.from_url() builds a brand-new ConnectionPool
    every time it is called — so the previous per-instance client meant a new
    pool and socket for the researcher, the writer, and the enforcer on every
    single generation, plus one per Celery task. One process-wide pool removes
    that churn; the client itself is thread-safe.
    """
    global _redis_pool
    if _redis_pool is None:
        with _redis_pool_lock:
            if _redis_pool is None:
                _redis_pool = redis.ConnectionPool.from_url(
                    os.getenv("REDIS_URL", "redis://redis:6379/0"),
                    decode_responses=True,
                )
    return redis.Redis(connection_pool=_redis_pool)


class BrandMetricsSQL(MetricPort):
    def __init__(self, business_id: str, content_type: str):
        self.business_id = business_id
        self.content_type = content_type
        self._redis = _get_redis_client()
        self._lock = Lock()

        # Two model tiers — cheap for per-doc extraction, better for synthesis
        self._extraction_llm = LLMSingleton.get("extraction")
        self._synthesis_llm = LLMSingleton.get("synthesis")

    @property
    def source(self) -> str:
        return "postgres"

    @property
    def _cache_key(self) -> str:
        return f"brand_context:{self.business_id}:{self.content_type}"

    @property
    def _stale_key(self) -> str:
        """Holds the previous context while a rebuild is pending."""
        return f"brand_context_stale:{self.business_id}:{self.content_type}"

    # ------------------------------------------------------------------ #
    #  Extraction — runs once per document, called from Celery task       #
    # ------------------------------------------------------------------ #

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def extract_and_save(self, doc_id: int, doc_content: str) -> bool:
        doc_hash = hashlib.sha256(doc_content.encode()).hexdigest()

        # --- idempotency check ---
        with get_db_session() as session:
            exists = session.query(BrandMetrics).filter_by(
                business_id=self.business_id,
                content_type=self.content_type,
                doc_hash=doc_hash,
            ).first()
            if exists:
                logger.info(
                    "Metrics already extracted for doc_hash=%s business=%s",
                    doc_hash[:8], self.business_id
                )
                return False

        # --- LLM extraction (cheap model) ---
        result = self._extraction_llm.invoke(
            METRICS_EXTRACTION.format(
                document=doc_content,
                content_type=self.content_type,
            )
        )

        try:
            raw = result.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            extracted = json.loads(raw.strip())
        except json.JSONDecodeError:
            logger.error("Extraction LLM returned invalid JSON — skipping persist")
            return False

        # --- verify the VERBATIM claims really are verbatim ---
        extracted, dropped = _ground_asset_bank(extracted, doc_content)
        if dropped:
            logger.warning(
                "Dropped %d ungrounded asset_bank claim(s) from doc_id=%s: %s",
                len(dropped), doc_id, "; ".join(dropped[:5]),
            )

        # --- persist new row ---
        try:
            with get_db_session() as session:
                row = BrandMetrics(
                    business_id=self.business_id,
                    content_type=self.content_type,
                    doc_id=doc_id,
                    doc_hash=doc_hash,
                    extracted=extracted,
                    score_weight=1.0,
                    source="document",
                    page_number=1,      
                    total_pages=1,      
                    page_hash=doc_hash,
                )
                session.add(row)
                session.commit()
                logger.info(
                    "Persisted metrics for doc_id=%s business=%s content_type=%s",
                    doc_id, self.business_id, self.content_type
                )
                return True

        except IntegrityError:
            # Race condition — another worker already inserted this hash
            logger.warning("Duplicate doc_hash on insert — race condition handled gracefully")
            return False

    # ------------------------------------------------------------------ #
    #  Synthesis — assembles and caches the brand context                 #
    # ------------------------------------------------------------------ #

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def build_and_cache_context(self) -> str:
        with get_db_session() as session:
            rows = (
                session.query(BrandMetrics)
                .filter_by(
                    business_id=self.business_id,
                    content_type=self.content_type,
                )
                .order_by(BrandMetrics.created_at.asc())
                .all()
            )

        if not rows:
            logger.info(
                "No metric rows found for business=%s content_type=%s",
                self.business_id, self.content_type,
            )
            return ""

        # Pass the most recent N profiles in full to the synthesis LLM.
        # Older ones are already captured in previous synthesis passes.
        recent = rows[-MAX_RECENT_PROFILES:]

        profiles_block = self._format_profiles_for_synthesis(recent)

        # Select appropriate synthesis prompt based on document count
        if len(rows) == 1:
            synthesis_prompt = METRICS_SYNTHESIS_SINGLE.format(
                business_id=self.business_id,
                content_type=self.content_type,
                profiles=profiles_block,
            )
        else:
            synthesis_prompt = METRICS_SYNTHESIS.format(
                business_id=self.business_id,
                content_type=self.content_type,
                total_documents=len(rows),
                profiles=profiles_block,
            )

        # Append the output structure template
        synthesis_prompt += "\n\n" + METRICS_SYNTHESIS_TEMPLATE

        result = self._synthesis_llm.invoke(synthesis_prompt)

        context = result.content
    
        # Persist to Postgres
        with get_db_session() as session:
            brain = session.query(BrandBrain).filter_by(
                business_id=self.business_id,
                content_type=self.content_type,
            ).first()
            
            if brain:
                brain.synthesis_text = context
                brain.profile_count = len(rows)
                brain.last_synthesis_at = datetime.utcnow()
                brain.version += 1
            else:
                brain = BrandBrain(
                    business_id=self.business_id,
                    content_type=self.content_type,
                    synthesis_text=context,
                    profile_count=len(rows),
                )
                session.add(brain)
            session.commit()
        
        # Append measured, non-LLM mechanics. The synthesis describes habits
        # qualitatively ("heavy use of exclamation marks"), and a qualitative
        # instruction is what produced a brand whose source uses 7.4
        # exclamation marks per 100 words generating copy at 21.4 — correct in
        # kind, wrong by 3x in degree. Counting the real corpus gives the
        # writer a number to aim at and the enforcer something to check.
        context = self._with_measured_mechanics(context)

        # Cache to Redis. The fresh value supersedes any stale copy that was
        # being served during the rebuild, so drop that in the same step.
        self._redis.set(self._cache_key, context, ex=CACHE_TTL)
        self._redis.delete(self._stale_key)
        return context

    # Emoji-ish codepoint ranges, wide enough for the pictographs and symbols
    # brands actually use in captions without dragging in ordinary punctuation.
    _EMOJI_RE = re.compile(
        "[\U0001F300-\U0001FAFF\U00002600-\U000027BF"
        "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF]"
    )

    # Register markers.
    #
    # This block counted punctuation, capitals and sentence length, none of
    # which say whether a brand writes plainly or corporately — and register is
    # the most recognisable part of a voice. A documentary distributor whose
    # corpus runs 0.89 nominalisations per 100 words produced copy at 6.17 ("a
    # diagnostic failure to prioritize the integrity of effort over immediate,
    # superficial gratification"), and every check passed it, because none of
    # them look at word choice.
    #
    # Nominalising suffixes are the clearest countable marker of the drift. The
    # corpus reaches for business, insurance, decision, temptation; the drift
    # reaches for gratification, superficiality, humiliation, velocity.
    _NOMINALISATION_RE = re.compile(
        r"\b\w{4,}(?:tion|sion|ment|ity|ance|ence|ness|ism|ivity)\b", re.I
    )
    _CONTRACTION_RE = re.compile(r"\b\w+['’](?:t|s|re|ve|ll|d|m)\b", re.I)

    def _measure_corpus_mechanics(self) -> str:
        """Count surface mechanics directly from this brand's own documents.

        Deterministic on purpose: these are facts about the corpus, not
        judgements about it, so they should be counted rather than inferred by
        a model. Returned as a brand-brain section the writer can aim at and
        the enforcer can check against.
        """
        from database import BrandDocument, DOC_ROLE_VOICE, get_db_session

        try:
            with get_db_session() as session:
                # Voice documents only. A reference document (product doc,
                # press kit) is written in its own register — measuring its
                # mechanics would set the brand's targets from a document the
                # brand never published, which is the same conflation the
                # doc_role split exists to prevent.
                docs = session.query(BrandDocument.file_content).filter_by(
                    business_id=self.business_id,
                    content_type=self.content_type,
                    doc_role=DOC_ROLE_VOICE,
                ).all()
            corpus = "\n".join(d[0] for d in docs if d and d[0])
        except Exception as e:
            logger.warning("Could not measure corpus mechanics: %s", e)
            return ""

        words = re.findall(r"[A-Za-z0-9']+", corpus)
        if len(words) < 50:
            return ""

        per100 = lambda n: round(100.0 * n / len(words), 1)
        letters = [c for c in corpus if c.isalpha()]
        sentences = [s for s in re.split(r"[.!?]+", corpus) if s.strip()]
        # Computed out here: an f-string expression cannot contain a backslash.
        # Split by position, because the two mean completely different things.
        # A bracket at the end of a line is an annotation — a caption archive
        # tagging an entry "[Launch post]", an ad script ending on "[DATE]".
        # A bracket mid-sentence with copy after it is an unresolved insertion
        # ("we can't sleep, [so] sorry in advance"). Measuring one rate for both
        # let inline artifacts through on any brand whose archive is annotated.
        annotation_brackets = inline_brackets = 0
        for line in corpus.splitlines():
            for m in re.finditer(r"\[[^\]]{1,60}\]", line):
                if line[m.end():].strip():
                    inline_brackets += 1
                else:
                    annotation_brackets += 1
        bracket_rate = per100(annotation_brackets)
        inline_bracket_rate = per100(inline_brackets)

        return (
            "# MEASURED MECHANICS\n"
            "Counted directly from this brand's uploaded documents. These are targets, "
            "not maximums to exceed: match the rate, do not amplify it. Writing well "
            "above these rates is as wrong as writing below them.\n"
            f"- exclamation_marks_per_100_words: {per100(corpus.count('!'))}\n"
            f"- question_marks_per_100_words: {per100(corpus.count('?'))}\n"
            f"- emoji_per_100_words: {per100(len(self._EMOJI_RE.findall(corpus)))}\n"
            f"- all_caps_words_per_100_words: "
            f"{per100(sum(1 for w in words if len(w) > 2 and w.isupper()))}\n"
            f"- uppercase_letter_ratio: "
            f"{round(sum(1 for c in letters if c.isupper()) / max(len(letters), 1), 2)}\n"
            f"- mean_words_per_sentence: {round(len(words) / max(len(sentences), 1), 1)}\n"
            # Register. These four decide whether the copy sounds like this
            # brand or like a consultancy, and nothing above them does.
            f"- nominalisations_per_100_words: "
            f"{per100(len(self._NOMINALISATION_RE.findall(corpus)))}\n"
            f"- four_plus_syllable_words_per_100_words: "
            f"{per100(sum(1 for w in words if _syllables(w) >= 4))}\n"
            f"- mean_word_length: "
            f"{round(sum(len(w) for w in words) / max(len(words), 1), 2)}\n"
            f"- contractions_per_100_words: "
            f"{per100(len(self._CONTRACTION_RE.findall(corpus)))}\n"
            # Some content types legitimately use bracketed slots as a
            # convention — an ad script carries "[DATE]", a caption archive
            # tags each entry with "[Launch post]". A press release does not.
            # Measuring the rate per content type is what lets the enforcer
            # tell a real convention from an unfilled template placeholder.
            f"- bracket_placeholders_per_100_words: {bracket_rate}\n"
            f"- inline_bracket_placeholders_per_100_words: {inline_bracket_rate}\n"
            f"- corpus_size_words: {len(words)}\n"
        )

    def _format_profiles_for_synthesis(self, rows: list) -> str:
        """
        Format metric rows into a structured block for the synthesis prompt.
        Each row becomes a clearly delimited section with its source and weight.
        """
        sections = []
        for i, row in enumerate(rows, 1):
            section = (
                f"## Profile {i} "
                f"(source: {row.source}, weight: {row.score_weight:.1f}, "
                f"date: {row.created_at.strftime('%Y-%m-%d')})\n"
                f"{json.dumps(row.extracted, indent=2)}"
            )
            sections.append(section)
        return "\n\n".join(sections)

    # ------------------------------------------------------------------ #
    #  Read path — called by writer and enforcer nodes                    #
    # ------------------------------------------------------------------ #

    def _with_measured_mechanics(self, context: str) -> str:
        """Attach the counted mechanics to a synthesis.

        MEASURED MECHANICS is computed here rather than by the synthesising
        model, and it is never stored in BrandBrain.synthesis_text — so every
        path that serves a context has to attach it. One did not.

        get_context() fell back to Postgres, returned synthesis_text bare, and
        cached that for a day. For those twenty-four hours every deterministic
        check that reads a measured rate had nothing to read and went quiet:
        check_measured_mechanics found no targets and returned no failures, and
        the placeholder gate could not tell a template slot from a brand
        convention. A press release shipped "[CITY, STATE] – [DATE]" and scored
        9.6, because the one check that would have caught it was silently off.

        Soft invalidation makes that path ordinary rather than rare: uploading a
        reference document clears the Redis key, the next request falls through
        to Postgres, and the gates stay quiet until something rebuilds.
        """
        measured = self._measure_corpus_mechanics()
        return f"{context}\n\n{measured}" if measured else context

    def get_context(self) -> str:
        # 1. Try Redis
        cached = self._redis.get(self._cache_key)
        if cached:
            return cached
        
        # 2. Try Postgres brain
        with get_db_session() as session:
            brain = session.query(BrandBrain).filter_by(
                business_id=self.business_id,
                content_type=self.content_type,
            ).first()
            
            # Count the actual number of extracted metric rows in the DB
            metrics_count = session.query(BrandMetrics).filter_by(
                business_id=self.business_id,
                content_type=self.content_type,
            ).count()

            # Only serve the Postgres brain if it is fully up to date
            if brain and brain.profile_count == metrics_count:
                # Attach the counted mechanics before caching. Serving
                # synthesis_text bare here disabled every gate that reads a
                # measured rate, for as long as the cache entry lived.
                context = self._with_measured_mechanics(brain.synthesis_text)
                self._redis.set(self._cache_key, context, ex=CACHE_TTL)
                return context
            elif brain:
                logger.info(
                    "Postgres brain is stale (brain_profiles=%s, actual_metrics=%s) — rebuilding",
                    brain.profile_count, metrics_count
                )

        # 3. Serve the superseded context if one is still within its grace
        # period. A queued rebuild will replace it shortly; blocking this
        # request on a full synthesis would cost far more than the staleness.
        stale = self._redis.get(self._stale_key)
        if stale:
            logger.info(
                "Serving stale brand context for business=%s content_type=%s "
                "while rebuild is pending",
                self.business_id, self.content_type,
            )
            return stale

        # 4. Nothing servable — build synchronously.
        return self.build_and_cache_context()

    def invalidate_cache(self, soft: bool = True):
        """Mark the cached brand context as superseded.

        soft=True (default) demotes the current value to a short-lived stale
        key instead of dropping it. A rebuild is normally already queued and
        debounced, so any generation arriving before it lands serves the
        previous brain immediately rather than blocking on a synchronous
        LLM synthesis. The rebuild clears the stale key when it completes.

        soft=False drops both keys outright — use it when the existing context
        must not be served again under any circumstances.
        """
        if soft:
            current = self._redis.get(self._cache_key)
            if current:
                self._redis.set(self._stale_key, current, ex=STALE_TTL)
        else:
            self._redis.delete(self._stale_key)

        self._redis.delete(self._cache_key)
        logger.info(
            "Cache invalidated (soft=%s) for business=%s content_type=%s",
            soft, self.business_id, self.content_type,
        )

    def delete_all(self):
        """Remove all metric rows, the synthesised brain, and the cache."""
        with get_db_session() as session:
            session.query(BrandMetrics).filter_by(
                business_id=self.business_id,
                content_type=self.content_type,
            ).delete()
            # The stored brain is derived entirely from the rows just deleted,
            # so leaving it behind leaves a voice profile with no corpus under
            # it. get_context() refuses to serve one whose profile_count no
            # longer matches the metric row count, which hides the problem
            # until the counts happen to line up again — re-uploading the same
            # corpus is enough — and then a reset brand brain silently serves
            # its pre-reset self.
            session.query(BrandBrain).filter_by(
                business_id=self.business_id,
                content_type=self.content_type,
            ).delete()
            session.commit()
        # Hard eviction: the metrics these were synthesised from are gone, so
        # the old context must not be served during any grace period.
        self.invalidate_cache(soft=False)
        logger.info(
            "Deleted all metrics for business=%s content_type=%s",
            self.business_id, self.content_type,
        )