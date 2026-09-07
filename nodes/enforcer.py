# nodes/enforcer.py
import json
import logging
import os
import re
from model import LLMSingleton
from brand_metrics import BrandMetricsSQL
from brand_rag import BrandRAG
from prompts.enforcer import ENFORCER_PROMPT, ENFORCER_HUMAN_DIRECTIVE
from graph.state import GraphState

logger = logging.getLogger(__name__)

from utils.observe import observe


def _parse_llm_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON from LLM output."""
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    return json.loads(raw.strip())


def _extract_permitted_claims(metrics: str) -> str:
    """
    Extract the BRAND ASSET BANK section from the brand brain and format it
    as an explicit closed list of permitted claims for the hallucination check.

    This converts the unstructured asset bank text into a numbered whitelist
    so the enforcer LLM can do exact lookup rather than relying on recall.
    Returns a formatted string ready for prompt injection.
    """
    # Extract the BRAND ASSET BANK section
    match = re.search(
        r"#\s*BRAND ASSET BANK\s*\n(.*?)(?=\n#\s+[A-Z]|\Z)",
        metrics,
        re.DOTALL | re.IGNORECASE
    )
    if not match:
        return (
            "No asset bank extracted yet. "
            "All specific numeric claims (client counts, percentages, ROI figures) "
            "in the content are UNVERIFIABLE and must be treated as hallucinations. "
            "Flag any specific number tied to brand experience."
        )

    asset_text = match.group(1).strip()

    # Parse individual claim lines — handle both bullet and dash formats
    lines = [l.strip().lstrip("-•*").strip() for l in asset_text.splitlines() if l.strip()]

    # Separate into categories for clarity
    social_proof     = []
    frameworks       = []
    values           = []
    financial_targets = []
    other            = []

    current_category = None
    for line in lines:
        upper = line.upper()
        if "SOCIAL PROOF" in upper:
            current_category = "social_proof"
            continue
        elif "FRAMEWORK" in upper or "METHODOLOG" in upper:
            current_category = "frameworks"
            continue
        elif "VALUE" in upper or "BELIEF" in upper:
            current_category = "values"
            continue
        elif "FINANCIAL TARGET" in upper or "PROJECTION" in upper:
            current_category = "financial_targets"
            continue
        elif line.startswith("#") or (line.isupper() and len(line) > 5):
            current_category = "other"
            continue

        if not line or line.startswith("#"):
            continue

        if current_category == "social_proof":
            social_proof.append(line)
        elif current_category == "frameworks":
            frameworks.append(line)
        elif current_category == "values":
            values.append(line)
        elif current_category == "financial_targets":
            financial_targets.append(line)
        else:
            other.append(line)

    sections = []

    if social_proof:
        numbered = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(social_proof))
        sections.append(f"PERMITTED SOCIAL PROOF CLAIMS (exact numbers the brand may claim):\n{numbered}")

    if financial_targets:
        numbered = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(financial_targets))
        sections.append(
            f"PERMITTED FINANCIAL TARGETS & PROJECTIONS (specific numbers, percentages, dollar amounts, "
            f"timeframes, and quantified outcomes the brand may use):\n{numbered}"
        )

    if frameworks:
        numbered = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(frameworks))
        sections.append(f"PERMITTED FRAMEWORKS & METHODOLOGIES:\n{numbered}")

    if values:
        numbered = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(values))
        sections.append(f"PERMITTED STATED VALUES & BELIEFS:\n{numbered}")

    if other:
        numbered = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(other))
        sections.append(f"OTHER PERMITTED CLAIMS:\n{numbered}")

    if not sections:
        return (
            "Asset bank found but could not be parsed into discrete claims. "
            "Treat ALL specific numeric claims in the content as unverified "
            "unless they appear verbatim in the brand metrics text above."
        )

    header = (
        "The following is the COMPLETE list of specific claims this brand is permitted to make.\n"
        "Any specific number, percentage, client count, or named framework NOT on this list "
        "is a hallucination and must be flagged. EXCEPTION: numbers that appear verbatim in "
        "the PERMITTED FINANCIAL TARGETS & PROJECTIONS list are always allowed.\n\n"
    )
    return header + "\n\n".join(sections)

# Generic corporate role-nouns, not brand names or topics — used only to tell
# a legitimate unnamed-role attribution ("said Meridian's Head of Development")
# apart from a specific invented person ("said Sarah Chen, Head of Studio").
# The former is a real, common brand pattern; the latter is a hallucinated
# identity. This list is about English business vocabulary, not any one
# brand's content, so it holds regardless of which documents get uploaded.
_ROLE_INDICATOR_WORDS = {
    "head", "director", "officer", "president", "ceo", "coo", "cto", "cfo",
    "chief", "manager", "spokesperson", "spokesman", "spokeswoman", "team",
    "department", "communications", "studio", "studios", "group", "committee",
    "board", "founder", "chairman", "chairwoman", "chairperson", "lead", "vp",
}

_ATTRIBUTION_VERBS = (
    r"(?:said|says|stated|states|explains|explained|noted|notes|added|"
    r"remarked|shared|according to)"
)
_NAME_SPAN = r"[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3}"
_QUOTE_THEN_ATTRIBUTION = re.compile(
    r'"([^"]{15,400})"\s*[,]?\s*' + _ATTRIBUTION_VERBS + r"\s+(" + _NAME_SPAN + r")"
)
_ATTRIBUTION_THEN_QUOTE = re.compile(
    r"(" + _NAME_SPAN + r")\s*[,]?\s*" + _ATTRIBUTION_VERBS + r'\s*[,:]?\s*"([^"]{15,400})"'
)


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"\b[a-z0-9][a-z0-9-]{1,62}\.(?:com|net|org|io|co|tv|film|studio|news|app|ai|uk|us|ca)\b",
    re.IGNORECASE,
)
# Loose on shape, strict on length: a real phone number carries at least 9
# digits, which keeps dates, episode counts and running times out of it.
#
# Separators are capped at two consecutive characters — enough for the ") " in
# "(555) 123-4567" — and a period followed by whitespace is excluded, because
# that is a sentence boundary rather than a separator. Without that exclusion
# "Episode 4. 2026-09-07" reads as a nine-digit phone number.
_PHONE_RE = re.compile(r"\(?\+?\d(?:(?:(?!\.\s)[\s().\-]){0,2}\d){8,}")
_MIN_PHONE_DIGITS = 9


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _find_ungrounded_contact_details(content: str, grounding_text: str) -> list[dict]:
    """Flag email addresses, phone numbers and URLs that appear nowhere in source.

    Denied a placeholder to fill, the writer invented one: a press release that
    previously shipped "[Press Contact Name]" came back with
    "press@meridianstudios.com" and "(555) 123-4567" instead — neither of which
    exists in any uploaded document. That is worse than the placeholder it
    replaced, because an unfilled slot is visibly unfinished while a plausible
    press address looks publishable.

    The numeric hallucination check does not cover these: a contact address is
    not a claim about brand experience, so it was never in scope. Checked
    deterministically because "does this exact string appear in the source" is
    an exact-match question.
    """
    if not grounding_text.strip():
        return []

    grounding_lower = grounding_text.lower()
    grounding_digits = _digits(grounding_text)
    findings, seen = [], set()

    def flag(value, kind):
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "value": value,
            "kind": kind,
            "message": (
                f"Invented {kind} {value!r} — it does not appear in any uploaded "
                "document or research result. Never fabricate contact details. "
                "Use the brand's own convention for directing enquiries instead, "
                "or omit the line."
            ),
        })

    emails = _EMAIL_RE.findall(content)
    for email in emails:
        if email.lower() not in grounding_lower:
            flag(email, "email address")

    # Strip emails before domain matching so one address is not reported twice.
    without_emails = _EMAIL_RE.sub(" ", content)

    for url in _URL_RE.findall(without_emails):
        cleaned = url.rstrip(".,;:)")
        if cleaned.lower() not in grounding_lower:
            flag(cleaned, "URL")

    for domain in _BARE_DOMAIN_RE.findall(without_emails):
        if domain.lower() not in grounding_lower:
            flag(domain, "web address")

    for phone in _PHONE_RE.findall(content):
        digits = _digits(phone)
        if len(digits) >= _MIN_PHONE_DIGITS and digits not in grounding_digits:
            flag(phone.strip(), "phone number")

    return findings[:6]


def _find_unverified_quote_attributions(content: str, grounding_text: str) -> list[dict]:
    """
    Catch a specific hallucination shape the numeric-claims check above never
    looks at: a quoted statement attributed to a named individual who does not
    exist anywhere in this generation's actual source material. E.g. a press
    release inventing "said Sarah Chen, Head of Studio" when no such person
    appears in the uploaded documents or researched context. Deterministic and
    topic/brand-agnostic — it only flags a NAME-shaped attribution (skipping
    generic unnamed role attributions via _ROLE_INDICATOR_WORDS) that is
    verifiably absent from the grounding text, so it works for any brand's
    documents without hardcoding who the "real" people are.
    """
    if not grounding_text.strip():
        return []  # nothing to verify against — skip rather than flag everything

    grounding_lower = grounding_text.lower()
    findings = []
    seen = set()

    for pattern, quote_group, who_group in (
        (_QUOTE_THEN_ATTRIBUTION, 1, 2),
        (_ATTRIBUTION_THEN_QUOTE, 2, 1),
    ):
        for m in pattern.finditer(content):
            # rstrip trailing punctuation the name-span regex can sweep up
            # (e.g. a sentence-ending period right after the name).
            who = m.group(who_group).strip().rstrip(".,;:")
            quote = m.group(quote_group).strip()
            key = who.lower()
            if key in seen:
                continue
            words = re.findall(r"[a-z']+", key)
            if any(w in _ROLE_INDICATOR_WORDS for w in words):
                continue  # unnamed role attribution — a normal brand pattern
            if key in grounding_lower:
                continue  # this name genuinely appears in the source material
            seen.add(key)
            findings.append({"who": who, "quote": quote})

    return findings


def _find_excerpt(content: str, markers, context_chars: int = 70) -> str:
    """Return a short excerpt around the first occurrence of a marker.

    Without a concrete excerpt, the writer's revision prompt only knows a
    violation exists "somewhere" — its own stale-feedback check then can't
    verify the violation is still present and treats the feedback as
    already resolved, so nothing gets fixed across revision loops.
    """
    for marker in (markers if isinstance(markers, (list, tuple)) else [markers]):
        idx = content.find(marker)
        if idx != -1:
            start = max(0, idx - context_chars)
            end = min(len(content), idx + len(marker) + context_chars)
            excerpt = content[start:end].strip()
            return f"...{excerpt}..." if (start > 0 or end < len(content)) else excerpt
    return ""


# Cues that a punctuation mark is prohibited, and cues that it is actively
# used. Ordered longest-first so "not used" is matched before "used".
_BAN_CUES = (
    "consistently absent", "are absent", "is absent", "absent",
    "not used", "never used", "never", "avoided", "avoid", "avoids",
    "omitted", "excluded", "prohibited", "banned", "no use of",
    "rarely", "rare",
)
_USE_CUES = (
    "heavy and consistent use", "heavy use", "heavily", "consistent use",
    "frequently", "frequent", "commonly", "common", "default",
    "dominant", "primarily", "occasionally", "occasional",
    "are used", "is used", "uses", "employs", "relies on",
)


def _mark_is_banned(rules_text: str, aliases: tuple) -> bool:
    """Decide whether ONE punctuation mark is prohibited by this brand.

    Previously both callers used a document-level test: if the PUNCTUATION
    HABITS block contained "avoid"/"absent" ANYWHERE, then every mark merely
    NAMED anywhere in that block was treated as banned. That silently inverts
    brands whose rules mix prohibition and prescription in one section. A real
    extracted example:

        "Heavy and consistent use of exclamation marks for emphasis ...
         Periods are rare, and semicolons, ellipses, and em dashes are
         consistently absent."

    The word "absent" (about semicolons) flipped the gate, "exclamation"
    appeared in the block, and every "!" was rewritten to "." — deleting the
    single most distinctive mark of that brand's voice, on every iteration.

    Instead, for each mention of the mark, compare the nearest ban cue and the
    nearest use cue within the same sentence and let the closer one decide. A
    mark described as used anywhere is never stripped: wrongly leaving a mark
    in costs a line of enforcer feedback, while wrongly removing one destroys
    the brand's voice with no way for the writer to recover it.
    """
    text = (rules_text or "").lower()
    if not text:
        return False

    verdicts = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for alias in aliases:
            for hit in re.finditer(re.escape(alias), sentence):
                pos = hit.start()

                def nearest(cues):
                    best = None
                    for cue in cues:
                        for c in re.finditer(re.escape(cue), sentence):
                            d = abs(c.start() - pos)
                            if best is None or d < best:
                                best = d
                    return best

                ban_d, use_d = nearest(_BAN_CUES), nearest(_USE_CUES)
                if ban_d is None and use_d is None:
                    continue
                if use_d is None:
                    verdicts.append("ban")
                elif ban_d is None:
                    verdicts.append("use")
                else:
                    verdicts.append("ban" if ban_d <= use_d else "use")

    if not verdicts:
        return False
    # Any evidence the brand actually uses the mark wins.
    return "use" not in verdicts and "ban" in verdicts


# A draft may reuse a brand's signature constructions (that is the product), but
# it must not reproduce its source material clause by clause. Measured baseline:
# two human-written documents from the same brand share at most a 4-word run,
# while an observed extractive draft lifted 15 consecutive words straight out of
# the product document it was given. 8 is comfortably clear of both the natural
# coincidence rate and the length of a typical signature phrase.
MAX_VERBATIM_SPAN_WORDS = 8

# Writer/Enforcer revision rounds. Tunable without a code change so the ceiling
# can be raised when a content type needs more passes — but note that extra
# rounds only help when the feedback is actionable: a loop that fails for the
# same reason three times usually fails for it five times too, at five times
# the latency. Must stay in step with graph.MAX_GRAPH_ITERATIONS, which reads
# the same variable.
MAX_ITERATIONS = int(os.getenv("MAX_REVISION_ITERATIONS", "3"))

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _tokens_with_offsets(text: str):
    return [(m.group(0).lower(), m.start(), m.end()) for m in _WORD_RE.finditer(text)]


def _quoted_regions(text: str):
    """Character ranges inside quotation marks.

    A press release legitimately quotes its source verbatim, and inventing a
    quote is already caught by _find_unverified_quote_attributions, so quoted
    spans are exempt from the extractive check rather than double-penalised.
    """
    return [
        (m.start(), m.end())
        for m in re.finditer(r'"[^"]{0,800}"|“[^”]{0,800}”', text)
    ]


def _find_extractive_spans(content: str, source: str,
                           max_span: int = MAX_VERBATIM_SPAN_WORDS,
                           limit: int = 5) -> list[dict]:
    """Runs of more than `max_span` consecutive words copied verbatim from source.

    Catches the failure mode where the writer summarises its research instead of
    writing from it — reproducing the source's phrasing (and, with it, whatever
    internal material the source happened to contain) rather than re-expressing
    the facts in the brand's own voice.
    """
    ctoks = _tokens_with_offsets(content)
    stoks = [t for t, _, _ in _tokens_with_offsets(source)]
    n = max_span + 1
    if len(ctoks) < n or len(stoks) < n:
        return []

    src_ngrams = {tuple(stoks[i:i + n]) for i in range(len(stoks) - n + 1)}
    quoted = _quoted_regions(content)
    words = [t for t, _, _ in ctoks]

    def inside_quote(a, b):
        return any(qs <= a and b <= qe for qs, qe in quoted)

    spans, i = [], 0
    while i <= len(words) - n:
        if tuple(words[i:i + n]) in src_ngrams:
            j = i + n
            while j < len(words) and tuple(words[j - n + 1:j + 1]) in src_ngrams:
                j += 1
            start, end = ctoks[i][1], ctoks[j - 1][2]
            if not inside_quote(start, end):
                spans.append({"length": j - i, "text": content[start:end]})
            i = j
        else:
            i += 1

    spans.sort(key=lambda s: -s["length"])
    return spans[:limit]


# How far above a brand's measured rate a draft may go before it counts as
# amplifying the voice rather than matching it. Generous, because short pieces
# swing naturally: only a clear overshoot should fail.
MECHANICS_TOLERANCE = 1.8
# Rates below this are too small for a ratio to mean anything on a short draft.
MECHANICS_MIN_RATE = 0.5


# Placeholders that are never a deliberate stylistic choice, whatever the brand.
_ALWAYS_PLACEHOLDER = re.compile(
    r"(lorem ipsum|\bTODO\b|\bTBC\b|\bXXX+\b|\{\{[^}]{1,60}\}\}"
    r"|\[\s*(?:insert|add|your|placeholder|tbd|tk)\b[^\]]{0,60}\])",
    re.IGNORECASE,
)
# Bracketed slots, which some content types use as a real convention.
_BRACKET_SLOT = re.compile(r"\[[^\]]{1,60}\]")
# Below this corpus rate, the brand does not use bracketed slots at all, so any
# in a draft are unfilled template text rather than house style.
BRACKET_CONVENTION_MIN_RATE = 0.3


def _find_unfilled_placeholders(content: str, metrics: str) -> list[dict]:
    """Catch template placeholders that were never filled in.

    An approved press release shipped with "[cast TBD]", "[Press Contact Name]",
    "[Press Email Address]" and "[Press Phone Number]" still in it and scored
    9.0/10, because nothing looked for them.

    Brackets alone cannot be the rule: the same studio's ad copy really does
    write "[DATE]" on an end card, and its caption archive tags entries with
    "[Launch post]". So bracketed slots are only failed when this content type's
    own corpus shows no such convention. Placeholders that are never intentional
    — lorem ipsum, TODO, {{mustache}}, "[insert ...]" — always fail.
    """
    failures = []

    for match in _ALWAYS_PLACEHOLDER.finditer(content):
        failures.append({
            "message": (
                f"Unfilled placeholder {match.group(0)!r} left in the content. "
                "Replace it with real material or remove the passage."
            ),
            "excerpt": _find_excerpt(content, match.group(0)),
        })

    section = re.search(
        r"#\s*MEASURED MECHANICS\s*\n(.*?)(?=\n#\s+[A-Z]|\Z)",
        metrics, re.DOTALL | re.IGNORECASE,
    )

    def measured(key):
        if not section:
            return None
        found = re.search(rf"{key}:\s*([0-9.]+)", section.group(1))
        return float(found.group(1)) if found else None

    annotation_rate = measured("bracket_placeholders_per_100_words")
    inline_rate = measured("inline_bracket_placeholders_per_100_words")

    # Position decides which rate applies. A bracket at the end of a line is an
    # annotation; one with copy after it on the same line is an insertion into
    # the prose. A brand can genuinely do the first while never doing the second
    # — Meridian's caption archive tags entries "[Launch post]" and its ad copy
    # ends on "[DATE]", but it never writes "[so] sorry in advance" mid-sentence.
    seen = {f["excerpt"] for f in failures}
    for line in content.splitlines():
        for match in _BRACKET_SLOT.finditer(line):
            token = match.group(0)
            is_inline = bool(line[match.end():].strip())
            rate = inline_rate if is_inline else annotation_rate
            # Without a measured rate we cannot tell convention from defect, so
            # stay quiet rather than fail a brand we have not measured.
            if rate is None or rate >= BRACKET_CONVENTION_MIN_RATE:
                continue
            if any(token in f["message"] for f in failures):
                continue
            excerpt = _find_excerpt(content, token)
            if excerpt in seen:
                continue
            seen.add(excerpt)
            where = (
                "mid-sentence, with copy continuing after it"
                if is_inline else "as a standalone annotation"
            )
            failures.append({
                "message": (
                    f"Unfilled placeholder {token!r} left in the content, {where}. "
                    f"This content type's own material uses brackets in that position "
                    f"at {rate} per 100 words, so this is template text rather than "
                    "house style. Write the real wording or remove it."
                ),
                "excerpt": excerpt,
            })

    return failures[:6]


def _check_measured_mechanics(content: str, metrics: str) -> list[dict]:
    """Flag surface mechanics used far more heavily than the brand's own corpus."""
    section = re.search(
        r"#\s*MEASURED MECHANICS\s*\n(.*?)(?=\n#\s+[A-Z]|\Z)",
        metrics, re.DOTALL | re.IGNORECASE,
    )
    if not section:
        return []

    targets = dict(re.findall(r"-\s*([a-z0-9_]+):\s*([0-9.]+)", section.group(1)))
    words = re.findall(r"[A-Za-z0-9']+", content)
    if len(words) < 20:
        return []

    def rate(n):
        return 100.0 * n / len(words)

    emoji_re = re.compile(
        "[\U0001F300-\U0001FAFF\U00002600-\U000027BF"
        "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF]"
    )
    observed = {
        "exclamation_marks_per_100_words": rate(content.count("!")),
        "question_marks_per_100_words": rate(content.count("?")),
        "emoji_per_100_words": rate(len(emoji_re.findall(content))),
        "all_caps_words_per_100_words": rate(
            sum(1 for w in words if len(w) > 2 and w.isupper())
        ),
    }

    failures = []
    for key, actual in observed.items():
        try:
            target = float(targets.get(key, ""))
        except ValueError:
            continue
        if target < MECHANICS_MIN_RATE or actual <= max(target * MECHANICS_TOLERANCE,
                                                        MECHANICS_MIN_RATE):
            continue
        label = key.replace("_per_100_words", "").replace("_", " ")
        failures.append({
            "message": (
                f"Brand uses {label} at {target:.1f} per 100 words; this draft uses "
                f"{actual:.1f} per 100 words ({actual / target:.1f}x). Match the "
                f"brand's rate rather than amplifying it — reduce to roughly "
                f"{target:.1f} per 100 words."
            ),
            "excerpt": "",
        })
    return failures


def _sanitize_banned_punctuation(content: str, metrics: str) -> tuple[str, list[str]]:
    """
    Deterministically strip/replace simple banned punctuation marks instead of
    asking the Writer to notice and fix them through another revision round.

    A revision pass fixes the ONE excerpt it was shown while free to introduce
    a fresh violation elsewhere in the same rewrite — observed in practice as
    an em-dash preflight failure recurring for 3 straight iterations even with
    the exact offending text quoted each time. A simple character-level rule
    like "no em-dashes" is unambiguous and mechanical, so it deserves a
    mechanical fix with a guaranteed outcome rather than a probabilistic one.

    Which marks are banned is still read dynamically from THIS brand's own
    extracted PUNCTUATION HABITS (same section _run_preflight_checks reads) —
    nothing here is hardcoded to any specific brand's rules or vocabulary.

    Only marks whose removal reliably keeps a sentence grammatical get a full
    rewrite here (exclamation, em-dash, semicolon, ellipsis). A banned
    question mark gets a blunter fallback: swap "?" for "." rather than
    rephrase into a true statement. That can occasionally read a little flat
    grammatically, but it guarantees the loop ends — observed in practice on
    an "ad" content type, where 3 straight revision iterations rewrote around
    one flagged question and reintroduced a different one each time, ending
    in a hard rejection the brand's own content would never have earned on
    substance. A guaranteed-compliant, slightly flat sentence beats a
    guaranteed rejection.

    Returns (sanitized content, list of human-readable fixes applied).
    """
    fixed = content
    applied = []

    punctuation_match = re.search(
        r"PUNCTUATION HABITS:\n(.*?)(?=\n[A-Z_]+:|\n#|\Z)", metrics, re.DOTALL | re.IGNORECASE
    )
    if punctuation_match:
        rules = punctuation_match.group(1)
        if _mark_is_banned(rules, ("exclamation",)) and "!" in fixed:
            fixed = fixed.replace("!", ".")
            applied.append("exclamation marks -> periods")

        if _mark_is_banned(rules, ("em dash", "em-dash", "emdash")) and ("—" in fixed or "--" in fixed):
            # A dash trailing off at the end of a sentence/line has nothing to
            # join with a comma — end the sentence instead of leaving one dangling.
            fixed = re.sub(r"\s*—\s*(?=[\"'”]?\s*(?:\n|$))", ".", fixed)
            fixed = re.sub(r"\s*--\s*(?=[\"'”]?\s*(?:\n|$))", ".", fixed)
            # Any dash still remaining is a mid-sentence aside — join with a comma.
            fixed = re.sub(r"\s*—\s*", ", ", fixed)
            fixed = re.sub(r"\s*--\s*", ", ", fixed)
            applied.append("em-dashes -> commas")

        if _mark_is_banned(rules, ("semicolon",)) and ";" in fixed:
            # "X; y..." (independent clauses) -> "X. Y..." (two sentences)
            fixed = re.sub(r";\s*([a-z])", lambda m: ". " + m.group(1).upper(), fixed)
            fixed = fixed.replace(";", ".")  # anything left (e.g. "; However") is already capitalized
            applied.append("semicolons -> sentence breaks")

        if _mark_is_banned(rules, ("ellipsis", "ellipses")) and ("..." in fixed or "…" in fixed):
            fixed = fixed.replace("…", "...")  # normalize to one form before splitting
            fixed = re.sub(r"\.\.\.\s*([a-z])", lambda m: ". " + m.group(1).upper(), fixed)
            fixed = re.sub(r"\.{2,}", ".", fixed)  # any remaining run not followed by a lowercase letter
            applied.append("ellipses -> periods")

    question_match = re.search(
        r"QUESTION USAGE:\n(.*?)(?=\n[A-Z_]+:|\n#|\Z)", metrics, re.DOTALL | re.IGNORECASE
    )
    if question_match:
        q_rules = question_match.group(1).lower()
        if ("avoid" in q_rules or "absent" in q_rules or "not use" in q_rules or "none" in q_rules) and "?" in fixed:
            fixed = fixed.replace("?", ".")
            applied.append("question marks -> periods (blunt fallback, not a rephrase)")

    if fixed != content:
        # Tidy up artifacts the substitutions above can leave behind: a
        # dangling comma right before sentence-ending punctuation, doubled
        # punctuation, or doubled spacing.
        fixed = re.sub(r",\s*([.,])", r"\1", fixed)
        fixed = re.sub(r"\.\s*\.", ".", fixed)
        fixed = re.sub(r"[ \t]{2,}", " ", fixed)
        fixed = re.sub(r"[ \t]+([.,])", r"\1", fixed)

    return fixed, applied


def _run_preflight_checks(content: str, metrics: str) -> list[dict]:
    """Run deterministic rule checks to catch strict anti-patterns before invoking the LLM.

    Each failure carries an "excerpt" of the offending text (not just the rule
    name) so the writer's revision prompt can find and fix the exact passage.
    """
    failures = []

    # 1. Check Punctuation
    punctuation_match = re.search(r"PUNCTUATION HABITS:\n(.*?)(?=\n[A-Z_]+:|\n#|\Z)", metrics, re.DOTALL | re.IGNORECASE)
    if punctuation_match:
        rules = punctuation_match.group(1)
        if _mark_is_banned(rules, ("exclamation",)) and "!" in content:
            failures.append({
                "message": "Brand avoids exclamation marks (!), but they were found.",
                "excerpt": _find_excerpt(content, "!"),
            })
        if _mark_is_banned(rules, ("em dash", "em-dash", "emdash")) and ("—" in content or "--" in content):
            failures.append({
                "message": "Brand avoids em-dashes (— or --), but they were found.",
                "excerpt": _find_excerpt(content, ["—", "--"]),
            })
        if _mark_is_banned(rules, ("semicolon",)) and ";" in content:
            failures.append({
                "message": "Brand avoids semicolons (;), but they were found.",
                "excerpt": _find_excerpt(content, ";"),
            })
        if _mark_is_banned(rules, ("ellipsis", "ellipses")) and ("..." in content or "…" in content):
            failures.append({
                "message": "Brand avoids ellipses (...), but they were found.",
                "excerpt": _find_excerpt(content, ["...", "…"]),
            })

    # 1b. Check measured mechanics — amplification, not just absence.
    # The punctuation rules above catch a mark the brand never uses. They say
    # nothing about a mark the brand DOES use being used three times as often,
    # which is its own voice failure: a brand at 7.4 exclamation marks per 100
    # words generated copy at 21.4 and every qualitative rule still "passed".
    failures.extend(_check_measured_mechanics(content, metrics))

    # 1c. Unfilled template placeholders. Deterministic because "[Press Contact
    # Name]" is unambiguous once the brand's own bracket convention is known.
    failures.extend(_find_unfilled_placeholders(content, metrics))

    # 2. Check Question Usage
    question_match = re.search(r"QUESTION USAGE:\n(.*?)(?=\n[A-Z_]+:|\n#|\Z)", metrics, re.DOTALL | re.IGNORECASE)
    if question_match:
        rules = question_match.group(1).lower()
        if ("avoid" in rules or "absent" in rules or "not use" in rules or "none" in rules) and "?" in content:
            failures.append({
                "message": "Brand strictly avoids questions, but a question mark (?) was found.",
                "excerpt": _find_excerpt(content, "?"),
            })

    return failures


@observe("enforcer_node")
def enforcer_node(state: GraphState) -> GraphState:
    from graph.deps import resolve_deps
    rag, analyzer, _memory = resolve_deps(state["business_id"], state["content_type"])

    content        = state["content"]
    metrics        = analyzer.get_context()
    iteration      = state.get("iteration", 1)
    max_iterations = MAX_ITERATIONS

    # 0. Deterministically fix simple banned punctuation before anything else
    # looks at the content, rather than flagging it and hoping a revision
    # round removes it (which fixes one excerpt but can reintroduce another
    # elsewhere). Mutate `state` immediately so every return path below —
    # preflight rejection, hallucination rejection, or full approval —
    # carries the corrected text forward instead of the original.
    content, punctuation_fixes = _sanitize_banned_punctuation(content, metrics)
    if punctuation_fixes:
        logger.info(
            "Auto-sanitized banned punctuation at iteration %d: %s",
            iteration, punctuation_fixes,
        )
    state = {**state, "content": content}

    # Build permitted claims whitelist from asset bank
    permitted_claims = _extract_permitted_claims(metrics)

    # 1a. Fabricated-attribution gate — a quote attributed to a named person
    # who appears nowhere in this generation's actual source material (the
    # brand brief or the Researcher's output) is a hallucination the numeric
    # claims check below cannot see, since it isn't a statistic. Checked
    # deterministically so it cannot be reasoned around the way an LLM-only
    # check was on the very first version of this evaluator.
    grounding_text = f"{metrics}\n\n{state.get('topic', '')}\n\n{state.get('research', '')}"
    fabricated_attributions = _find_unverified_quote_attributions(content, grounding_text)
    if fabricated_attributions:
        logger.warning(
            "Fabricated quote attribution(s) at iteration %d: %s",
            iteration, [f["who"] for f in fabricated_attributions],
        )
        feedback = (
            "HALLUCINATION DETECTED — the content attributes a quote to a named "
            "person who does not appear anywhere in the source material:\n"
        )
        flagged_lines = []
        for f in fabricated_attributions:
            feedback += f"  - \"{f['quote']}\" — attributed to \"{f['who']}\", who is not in any uploaded document or research result.\n"
            flagged_lines.append(f"\"{f['quote']}\" — fabricated attribution to \"{f['who']}\"")
        feedback += (
            "\nRemove the invented name. Either attribute the statement to an unnamed "
            "role already used in the brand's material (e.g. a title without a name), "
            "or drop the quote and state the point as a general brand observation."
        )
        return {
            **state,
            "approved": False,
            "score": 0.0,
            "style_match": 0.0,
            "tone_match": 0.0,
            "structure_match": 0.0,
            "signature_match": 0.0,
            "feedback": feedback,
            "flagged_passages": "\n".join(flagged_lines),
            "creative_angle": "unknown",
        }

    # 1a-1. Fabricated contact details. Grouped with the attribution gate
    # because it is the same class of failure — inventing a verifiable-looking
    # real-world detail — and it must never ship, so it returns early rather
    # than becoming a score deduction that max-iteration approval can wave past.
    ungrounded_contacts = _find_ungrounded_contact_details(content, grounding_text)
    if ungrounded_contacts:
        logger.warning(
            "FABRICATED CONTACT DETAILS at iteration %d: %s",
            iteration, [c["value"] for c in ungrounded_contacts],
        )
        feedback = (
            "FABRICATED CONTACT DETAILS — this draft invents contact information "
            "that exists nowhere in the source material:\n"
        )
        flagged_lines = []
        for c in ungrounded_contacts:
            feedback += f"  - {c['message']}\n"
            flagged_lines.append(f"\"{c['value']}\" — invented {c['kind']}")
        feedback += (
            "\nRemove every invented address, number and URL. If the brand's own "
            "material shows how it directs enquiries (for example, naming a team "
            "rather than an individual), follow that. If it does not, leave the "
            "detail out entirely. Do not substitute a placeholder either."
        )
        return {
            **state,
            "approved": False,
            "score": 0.0,
            "style_match": 0.0,
            "tone_match": 0.0,
            "structure_match": 0.0,
            "signature_match": 0.0,
            "feedback": feedback,
            "flagged_passages": "\n".join(flagged_lines),
            "creative_angle": "unknown",
        }

    # 1a-2. Extractive-copying gate — the draft must be written FROM the
    # research, not assembled OUT of it. Checked deterministically for the same
    # reason as the punctuation rules: "do not reuse more than 8 consecutive
    # words" is mechanical and unambiguous, so it deserves a guaranteed check
    # rather than a probabilistic one. Compared against the research only, never
    # against the brand brain — reusing the brand's signature constructions is
    # the point of the product, reproducing its source documents is not.
    research_text = state.get("research", "") or ""
    extractive_spans = _find_extractive_spans(content, research_text)
    if extractive_spans:
        logger.warning(
            "EXTRACTIVE COPYING at iteration %d — %d span(s), longest %d words",
            iteration, len(extractive_spans), extractive_spans[0]["length"],
        )
        feedback = (
            "EXTRACTIVE COPYING DETECTED — this draft reproduces its source material "
            "verbatim instead of re-expressing it in the brand's voice. Passages of "
            f"more than {MAX_VERBATIM_SPAN_WORDS} consecutive words are copied from the "
            "research:\n"
        )
        flagged_lines = []
        for span in extractive_spans:
            feedback += f"  - ({span['length']} words) \"{span['text']}\"\n"
            flagged_lines.append(
                f"\"{span['text']}\" — {span['length']} consecutive words copied from source"
            )
        feedback += (
            "\nRewrite each passage: keep the FACT, discard the source's wording and "
            "sentence shape, and state it the way this brand would state it. Source "
            "documents are briefing material, not copy to be pasted. If a passage is "
            "internal planning detail (strategy notes, production logistics, positioning "
            "rationale) rather than something the brand would publish, drop it entirely "
            "instead of rephrasing it."
        )
        return {
            **state,
            "approved": False,
            "score": 0.0,
            "style_match": 0.0,
            "tone_match": 0.0,
            "structure_match": 0.0,
            "signature_match": 0.0,
            "feedback": feedback,
            "flagged_passages": "\n".join(flagged_lines),
            "creative_angle": "unknown",
        }

    # 1b. Run Fast Pre-flight Checks (Bypass LLM if failed)
    preflight_failures = _run_preflight_checks(content, metrics)
    if preflight_failures:
        logger.warning(
            "Pre-flight checks failed at iteration %d: %s",
            iteration, [f["message"] for f in preflight_failures],
        )
        feedback = "DETERMINISTIC ANTI-PATTERN DETECTED — The content violates strict mechanical rules:\n"
        flagged_lines = []
        for failure in preflight_failures:
            feedback += f"- {failure['message']}\n"
            if failure["excerpt"]:
                feedback += f"  Offending text: \"{failure['excerpt']}\"\n"
                flagged_lines.append(f"\"{failure['excerpt']}\" — {failure['message']}")
        feedback += "\nPlease fix these formatting errors. No further evaluation was performed."

        return {
            **state,
            "approved": False,
            "score": 0.0,
            "style_match": 0.0,
            "tone_match": 0.0,
            "structure_match": 0.0,
            "signature_match": 0.0,
            "feedback": feedback,
            "flagged_passages": "\n".join(flagged_lines) if flagged_lines else "Multiple formatting violations.",
            "creative_angle": "unknown"
        }

    # A human reviewer's rejection note. When present it becomes a hard gate
    # ahead of the brand-pattern scoring: the enforcer's job is no longer only
    # "does this match the brand" but also "was the change the human asked for
    # actually made".
    human_feedback = (state.get("human_feedback") or "").strip()
    human_directive = (
        ENFORCER_HUMAN_DIRECTIVE.format(human_feedback=human_feedback)
        if human_feedback else ""
    )

    # The enforcer no longer uses raw RAG examples, relying strictly on synthesized rules.
    result = LLMSingleton.get("enforcement").invoke(
        ENFORCER_PROMPT.format(
            metrics=metrics,
            content=content,
            topic=state.get("topic", "") or "No topic provided.",
            research=state.get("research", "") or "No research/source material was provided for this generation.",
            permitted_claims=permitted_claims,
            human_directive=human_directive
        )
    )

    logger.info("ENFORCER_RAW_OUTPUT:\n%s", result.content[:2000])

    try:
        evaluation = _parse_llm_json(result.content)
    except Exception:
        # A single malformed response (truncated JSON, stray commentary) shouldn't
        # tank an otherwise-passing draft with an automatic hallucination fail —
        # ask the model to re-emit its own evaluation as clean JSON once before
        # giving up and failing closed.
        logger.warning("Failed to parse enforcer output — retrying once with a repair prompt")
        try:
            repair = LLMSingleton.get("enforcement").invoke(
                "Your previous response could not be parsed as JSON. Re-emit your evaluation "
                "as a single valid JSON object only — no markdown fences, no commentary, no "
                "text before or after the braces. Here is what you previously returned:\n\n"
                + result.content
            )
            evaluation = _parse_llm_json(repair.content)
            logger.info("Enforcer JSON repaired successfully on retry")
        except Exception:
            logger.error("Failed to parse enforcer output after retry, defaulting to NOT approved")
            evaluation = {
                "hallucination_check": {"verdict": "FAIL", "hallucinated_claims": ["Parse failure — content requires re-evaluation."]},
                "approved": False,
                "score": 0.0,
                "style_match": 0.0,
                "tone_match": 0.0,
                "structure_match": 0.0,
                "signature_match": 0.0,
                "dimension_details": {},
                "flagged_passages": [],
                "feedback": "Enforcer evaluation failed - content requires re-evaluation.",
                "creative_angle": "unknown"
            }

    # A syntactically valid JSON response can still omit keys. Normalise before
    # the gates below index into it, so a partial response fails closed
    # (not approved) rather than raising KeyError mid-pipeline.
    evaluation.setdefault("approved", False)
    evaluation.setdefault("score", 0.0)

    # Hard gate: a reviewer directive that was not carried out overrides the
    # brand-pattern scores. Enforced here in code rather than trusting the model
    # to have honoured APPROVAL RULE 7 on its own — the same reason the
    # hallucination verdict below is re-applied deterministically.
    if human_feedback:
        directive_verdict = str(evaluation.get("directive_compliance", "FAIL")).strip().upper()
        if directive_verdict.startswith("FAIL"):
            logger.warning(
                "REVIEWER DIRECTIVE NOT MET at iteration %d — forcing rejection", iteration,
            )
            evaluation["approved"] = False
            evaluation["score"] = min(float(evaluation.get("score", 0.0)), 5.0)
            directive_feedback = (
                "REVIEWER DIRECTIVE NOT MET — a human reviewer asked for a specific "
                "change and this draft does not yet satisfy it:\n"
                f"  {human_feedback}\n"
                "Apply that change before anything else. It outranks the brand patterns.\n"
            )
            existing = evaluation.get("feedback", "")
            evaluation["feedback"] = directive_feedback + (
                f"\nADDITIONAL FEEDBACK:\n{existing}" if existing else ""
            )
        else:
            logger.info("Reviewer directive satisfied at iteration %d", iteration)

    # Hard gate: internal planning material must not reach published copy.
    # Re-applied in code for the same reason as the other gates — the model
    # returned approved:true alongside a directive_compliance FAIL once already,
    # so an APPROVAL RULE is not by itself an enforcement mechanism.
    publishability = str(evaluation.get("publishability", "PASS")).strip().upper()
    if publishability.startswith("FAIL"):
        logger.warning(
            "PUBLISHABILITY FAIL at iteration %d — internal material in output", iteration,
        )
        evaluation["approved"] = False
        evaluation["score"] = min(float(evaluation.get("score", 0.0)), 5.0)
        publish_feedback = (
            "INTERNAL MATERIAL IN PUBLISHED COPY — this draft restates something the "
            "brand told itself rather than something it tells its audience. Marketing "
            "directives, positioning rationale, audience targeting, test findings and "
            "production logistics are context for writing, never content to publish.\n"
            "DELETE the offending sentences outright. Do NOT reword them — rewording an "
            "internal fact still discloses it, and that is why this draft failed after "
            "previous attempts to soften the wording. Removing a sentence entirely is the "
            "only fix. The piece is allowed to be shorter.\n"
        )
        # Name the exact sentences. A generic instruction to remove internal
        # material has already been observed to fail across three revision
        # rounds; the writer needs to be told precisely what to cut.
        offending = evaluation.get("flagged_passages") or []
        if isinstance(offending, list) and offending:
            publish_feedback += "Delete these exactly:\n"
            for passage in offending[:6]:
                publish_feedback += f"  - {passage}\n"
        elif isinstance(publishability, str) and len(publishability) > 8:
            publish_feedback += f"Reported by the publishability check: {publishability}\n"
        existing = evaluation.get("feedback", "")
        evaluation["feedback"] = publish_feedback + (
            f"\nADDITIONAL FEEDBACK:\n{existing}" if existing else ""
        )

    # Hard gate: hallucination failure overrides score and approval
    hallucination = evaluation.get("hallucination_check", {})
    hallucination_verdict = hallucination.get("verdict", "FAIL").upper()

    if hallucination_verdict == "FAIL":
        hallucinated = hallucination.get("hallucinated_claims", [])
        logger.warning(
            "HALLUCINATION DETECTED at iteration %d — %d fabricated claim(s): %s",
            iteration,
            len(hallucinated),
            hallucinated
        )
        # Cap score hard at 4.0 and force rejection regardless of other dimensions
        evaluation["approved"] = False
        evaluation["score"] = min(evaluation.get("score", 0.0), 4.0)

        # Build actionable feedback pointing to permitted alternatives
        hallucination_feedback = (
            "HALLUCINATION DETECTED — content contains fabricated claims not in the brand's asset bank.\n"
            "Fabricated claims found:\n"
        )
        for claim in hallucinated:
            hallucination_feedback += f"  - {claim}\n"
        hallucination_feedback += (
            "\nReplace all fabricated claims with permitted alternatives from the brand asset bank. "
            "Use only exact client counts, percentages, and framework names from the permitted claims list. "
            "If no suitable permitted claim exists for a numbered point, rewrite that point to use "
            "a general brand observation without specific numbers."
        )
        # Prepend hallucination feedback — it is the priority fix
        existing_feedback = evaluation.get("feedback", "")
        evaluation["feedback"] = hallucination_feedback + (
            f"\n\nADDITIONAL FEEDBACK:\n{existing_feedback}" if existing_feedback else ""
        )
        

    # Score gate: force revision if below threshold and iterations remain
    MIN_SCORE = 8.0
    if evaluation.get("score", 0.0) < MIN_SCORE and iteration < max_iterations:
        logger.info(
            "Score %.1f below threshold %.1f at iteration %d — forcing revision",
            evaluation["score"], MIN_SCORE, iteration
        )
        evaluation["approved"] = False
        if not evaluation.get("feedback"):
            evaluation["feedback"] = (
                "Content does not sufficiently match the brand voice. "
                "Focus on: anchoring claims to brand experience with specific permitted data, "
                "matching the brand's opening and closing patterns, "
                "and weaving in signature constructions naturally."
            )

    # Hard cap: at max iterations, force approval ONLY for quality shortfalls.
    # A merely low brand-match score is a "good enough, ship it" call. These
    # three are not — they are reasons the content must not be published at all,
    # and running out of revision attempts does not make them acceptable.
    # (Observed: a draft that failed the publishability gate at iteration 3 was
    # capped to 5.0 by that gate and then force-approved anyway, shipping the
    # internal material the gate had just caught.)
    blocking = []
    if hallucination_verdict == "FAIL":
        blocking.append("fabricated claims")
    if publishability.startswith("FAIL"):
        blocking.append("internal material in published copy")
    if human_feedback and str(
        evaluation.get("directive_compliance", "FAIL")
    ).strip().upper().startswith("FAIL"):
        blocking.append("reviewer directive not met")

    if not evaluation["approved"] and iteration >= max_iterations:
        if blocking:
            logger.error(
                "Max iterations reached but blocking issues remain (%s) — NOT approving. "
                "This content requires a human rewrite before it can be deployed.",
                "; ".join(blocking),
            )
            # Do not force approve — these must never ship unreviewed
        else:
            logger.warning("Max iterations reached (quality only), forcing approval")
            evaluation["approved"] = True

    logger.info(
        "Enforcer: hallucination=%s approved=%s score=%.1f style=%.1f tone=%.1f structure=%.1f signature=%.1f iteration=%d",
        hallucination_verdict,
        evaluation["approved"],
        evaluation["score"],
        evaluation.get("style_match", 0.0),
        evaluation.get("tone_match", 0.0),
        evaluation.get("structure_match", 0.0),
        evaluation.get("signature_match", 0.0),
        iteration
    )

    # Format flagged passages for the writer revision prompt
    flagged = evaluation.get("flagged_passages", [])
    if isinstance(flagged, list):
        flagged_str = "\n".join(f"- {p}" for p in flagged) if flagged else "No specific passages flagged."
    else:
        flagged_str = str(flagged) if flagged else "No specific passages flagged."

    return {
        **state,
        "approved":        evaluation["approved"],
        "score":           evaluation["score"],
        "style_match":     evaluation.get("style_match", 0.0),
        "tone_match":      evaluation.get("tone_match", 0.0),
        "structure_match": evaluation.get("structure_match", 0.0),
        "signature_match": evaluation.get("signature_match", 0.0),
        "feedback":        evaluation.get("feedback", ""),
        "flagged_passages": flagged_str,
        "creative_angle":  evaluation.get("creative_angle", "unknown")
    }