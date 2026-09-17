"""Readers for the synthesized Brand Brain.

The Brand Brain is a single markdown-ish document produced by
brand_metrics.build_and_cache_context(). These helpers pull individual sections
out of it. They live here rather than beside any one node because the
Researcher, Writer and Enforcer all read the same document, and the Researcher
was previously importing a private helper out of the Writer to do it.
"""
import logging
import re

logger = logging.getLogger(__name__)


def extract_section(text: str, header: str) -> str:
    """
    Extract a named section from the brand_brains synthesis text.
    Sections are delimited by lines starting with '#'.
    
    Matches by checking if the line (stripped of '#' and whitespace)
    starts with the header — avoids partial matches like 'OPENING'
    matching 'OPENING MOVE' inside section_patterns.
    """
    lines = text.split('\n')
    capture = False
    result = []
    for line in lines:
        # Normalize: strip '#' prefix and whitespace, then check if header matches
        normalized = line.strip().lstrip('#').strip()
        if normalized.upper().startswith(header.upper()):
            capture = True
            continue
        if capture and line.strip().startswith('#'):
            break
        if capture:
            result.append(line)
    return '\n'.join(result).strip()


def extract_brand_name(metrics: str) -> str:
    """
    Extract the brand name, tolerating a common synthesis-LLM slip where the
    name is folded straight into the section header (e.g. '# MERIDIAN STUDIOS')
    instead of the requested '# BRAND NAME' header followed by the name on its
    own line. Without this fallback, that formatting drift silently defaults
    the writer to a generic 'our agency' placeholder in the generated copy.
    """
    name = extract_section(metrics, "BRAND NAME")
    if name and "not extracted" not in name.lower() and "inject manually" not in name.lower():
        return name.strip()

    first_header = re.match(r"\s*#\s*(.+)", metrics)
    if first_header:
        candidate = first_header.group(1).strip()
        if candidate and candidate.upper() not in ("BRAND NAME", "BRAND ASSET BANK"):
            return candidate.title() if candidate.isupper() else candidate

    return ""


# A "named framework" that is really a section heading from one document: act
# titles, part and chapter numbers, scene labels. Harvested once as brand assets,
# they became a closed list the enforcer forced onto unrelated stories — a
# Nigerian campus script was ordered to reuse The Bourne Supremacy's act titles.
_DOCUMENT_STRUCTURE_TITLE = re.compile(
    r"^\s*[-*]?\s*(ACT|PART|CHAPTER|SCENE|EPISODE|SECTION|STRUCTURAL LESSON|FADE IN|FADE OUT|CUT TO)\b",
    re.IGNORECASE,
)
# The synthesis records how many source documents an asset appeared in.
_APPEARS_IN = re.compile(r"appears in (\d+)\s*/\s*(\d+) profiles", re.IGNORECASE)


def is_brand_asset(line: str) -> bool:
    """Whether an asset-bank line is a brand-level fact rather than one document's content.

    Two rejections, both learned from the same failure: a heading is a heading
    however it is phrased, and an item found in a single document out of several
    is that document's subject matter, not something the brand claims.
    """
    stripped = line.strip().lstrip("-• ").strip()
    if not stripped:
        return False
    if _DOCUMENT_STRUCTURE_TITLE.match(stripped):
        return False
    appears = _APPEARS_IN.search(stripped)
    if appears and int(appears.group(2)) > 1 and int(appears.group(1)) <= 1:
        return False
    return True


def filter_asset_bank(asset_text: str) -> str:
    """Drop document-structure titles and one-off items from an asset bank block."""
    kept = []
    for line in asset_text.splitlines():
        stripped = line.strip()
        is_item = stripped.startswith(("-", "•")) or (stripped and stripped[0].isdigit() and "." in stripped[:3])
        if is_item and not is_brand_asset(stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


def extract_asset_bank(metrics: str) -> str:
    """
    Extract the BRAND ASSET BANK section and format it as an explicit
    closed list of permitted claims for injection into the writer prompt.
    This prevents the writer from hallucinating client counts, percentages,
    and named frameworks by giving it only the facts it is allowed to use.
    Now also extracts FINANCIAL TARGETS & PROJECTIONS so proposal-type content
    can use specific numbers, dollar amounts, and timeframes without triggering
    the hallucination gate.
    """
    match = re.search(
        r"#\s*BRAND ASSET BANK\s*\n(.*?)(?=\n#\s+[A-Z]|\Z)",
        metrics,
        re.DOTALL | re.IGNORECASE
    )
    if not match:
        return (
            "No asset bank available yet. "
            "Do NOT invent specific numbers, client counts, percentages, or ROI figures. "
            "Use only general brand observations without specific data points."
        )
    asset_text = filter_asset_bank(match.group(1).strip())

    # Check whether a FINANCIAL TARGETS section exists in the asset bank
    has_financial = bool(re.search(
        r"FINANCIAL TARGETS", asset_text, re.IGNORECASE
    ))

    header = (
        "PERMITTED BRAND CLAIMS — use ONLY these exact numbers and facts when writing brand experience claims.\n"
        "Do NOT invent any number, percentage, client count, timeframe, or framework name not listed here.\n"
    )
    if has_financial:
        header += (
            "FINANCIAL TARGETS & PROJECTIONS listed below are explicitly permitted — "
            "use them verbatim when writing program objectives, financial summaries, or roadmap timeframes.\n"
        )

    return header + "\n" + asset_text


# Sections of the Brain that are supposed to describe the brand's writing
# rather than reproduce it. The synthesis prompt asks for moves described
# abstractly, and asking is not the same as getting: from four screenplays it
# returned "the 'He realizes that...' declarative phrase", the enforcer told a
# draft to use it, and the draft duly wrote "He realizes that his wallet is
# gone" — a phrase lifted from somebody else's story, arriving by the one route
# nothing was checking.
_DESCRIBED_SECTIONS = ("SIGNATURE CONSTRUCTIONS", "STRUCTURAL PATTERNS", "THINKING TEMPLATES")

# A run this long shared with the corpus is a quotation, not a description.
# Four words is where ordinary English overlap ends: "the way that it" is
# coincidence, "He realizes that his" is the corpus.
MAX_DESCRIBED_RUN = 4

# The commoner shape is shorter than that and announces itself: a construction
# written as a quoted fragment. "The 'He realizes that...' declarative phrase"
# shares only three words with the corpus — under any sane run threshold — and
# is still the corpus's sentence, handed to the writer to reuse.
_OPEN_QUOTE, _CLOSE_QUOTE = "['‘“\"]", "['’”\"]"
_QUOTED_FRAGMENT = re.compile(_OPEN_QUOTE + "([^'’”\"]{4,80})" + _CLOSE_QUOTE)


def _is_quotation(line: str, corpus: str) -> bool:
    """Whether a line meant to describe a move reproduces one instead."""
    from utils.voice_spec import longest_shared_run

    if longest_shared_run(line, corpus) > MAX_DESCRIBED_RUN:
        return True
    for fragment in _QUOTED_FRAGMENT.findall(line):
        words = re.findall(r"[A-Za-z0-9']+", fragment)
        if len(words) >= 2 and longest_shared_run(fragment, corpus) >= len(words):
            return True
    return False


# Word-count prescriptions an extraction or synthesis model wrote into the
# Brain: "(3-8 words)", "10-15 words", "2 to 4 sentences". They are invented —
# the same corpus produced "10-15" in one round and "10-20" in the next — and
# they are the thing the voice pass is not allowed to reason from, so they are
# removed before it sees them rather than left to its discretion.
_INVENTED_COUNT = re.compile(
    r"\s*[\(\[]?\b\d+\s*(?:-|–|—|to)\s*\d+\s+(?:words?|sentences?|lines?|syllables?)\b[\)\]]?",
    re.IGNORECASE,
)
_SINGLE_COUNT = re.compile(
    r"\s*[\(\[]?\b(?:under|over|about|around|roughly|at least|at most|no more than|fewer than|"
    r"more than)\s+\d+\s+(?:words?|sentences?|lines?|syllables?)\b[\)\]]?",
    re.IGNORECASE,
)


def strip_invented_counts(text: str) -> str:
    """Remove word-count prescriptions from a passage of the Brand Brain."""
    if not text:
        return text
    cleaned = _SINGLE_COUNT.sub("", _INVENTED_COUNT.sub("", text))
    return re.sub(r"[ \t]{2,}", " ", cleaned)


def strip_quoted_constructions(brain: str, corpus: str) -> str:
    """Remove lines from the descriptive sections that quote the corpus instead.

    The brand's own sentences must never reach a prompt. Everywhere else this is
    guaranteed by construction — the voice spec is measured, the asset bank is
    grounded per claim — but the synthesis sections are free text from a model,
    so what it returns is checked rather than trusted.
    """
    from utils.voice_spec import longest_shared_run

    if not brain or not corpus:
        return brain
    out, in_section, dropped = [], False, 0
    for line in brain.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_section = any(name in stripped.upper() for name in _DESCRIBED_SECTIONS)
            out.append(line)
            continue
        if in_section and stripped and _is_quotation(stripped, corpus):
            dropped += 1
            continue
        out.append(line)
    if dropped:
        logger.info("Dropped %d quoted line(s) from the Brain's descriptive sections", dropped)
    return "\n".join(out)


def extract_permitted_claims(metrics: str, content_type: str = "") -> str:
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
    from schema import is_narrative

    if is_narrative(content_type):
        # A story has no client counts or methodologies to police, and its
        # section titles are the writer's own. Held to a closed list, a script
        # was ordered to reuse another story's act titles as "permitted
        # frameworks". Invented numbers and contact details are still caught by
        # the deterministic checks, which do not need a list.
        return (
            "This is narrative content. There is no closed list of permitted claims: section titles, act names "
            "and scene headings are the writer's own and must never be flagged as fabricated. "
            "Flag a specific number, statistic, date or contact detail only when it states something about the "
            "real world (a business, a person, a product) and appears nowhere in the source material."
        )

    if not match:
        return (
            "No asset bank extracted yet. "
            "All specific numeric claims (client counts, percentages, ROI figures) "
            "in the content are UNVERIFIABLE and must be treated as hallucinations. "
            "Flag any specific number tied to brand experience."
        )

    asset_text = filter_asset_bank(match.group(1).strip())

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


def measured_mechanics_section(metrics: str) -> str | None:
    """The MEASURED MECHANICS block, or None when the brain has no such section.

    Written by brand_metrics._measure_corpus_mechanics() by counting the brand's
    own documents, so the values are measurements rather than model judgements.
    Both the mechanics check and the placeholder check read it.
    """
    match = re.search(
        r"#\s*MEASURED MECHANICS\s*\n(.*?)(?=\n#\s+[A-Z]|\Z)",
        metrics, re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else None


# A synthesised brain writes its prose sections under all-caps headers, and
# those headers contain spaces, ampersands and apostrophes: "PUNCTUATION
# HABITS:", "METAPHOR & ANALOGY:", "DON'T:". A next-header lookahead of
# [A-Z_]+: matches none of them.
#
# That is not a cosmetic miss. The rules that decide whether this brand permits
# a punctuation mark are read out of one of these sections, so a lookahead that
# cannot find the next header runs the capture to the end of the brain and reads
# every later section as though it were punctuation rules. Live consequence: the
# QUESTION USAGE capture ran 2085 characters instead of 330, picked up "avoids
# extended, complex, or overly decorative metaphors" from METAPHOR & ANALOGY and
# "heading hierarchies are generally absent" from SECTION PATTERN, and the word
# "avoid" appearing anywhere in that span stripped every question mark out of
# every draft. A trailer shipped opening on "Did you think you knew this story."
_SECTION_BOUNDARY = r"(?=\n[A-Z][A-Z0-9 &'/_-]*:|\n#|\Z)"


def brand_prose_section(metrics: str, header: str) -> str | None:
    """The body of one all-caps prose section of the brain, or None if absent.

    Stops at the next such header, at a '#' block, or at the end of the text.
    """
    match = re.search(
        rf"{re.escape(header)}:\n(.*?){_SECTION_BOUNDARY}",
        metrics, re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else None


_NAME_EVIDENCE_CACHE: dict[str, str] = {}


def brand_name_evidence(business_id: str) -> str:
    """Every document this business has uploaded, across all content types.

    Used only to decide which words belong to names. That is a property of the
    brand, not of one content type, and asking within a single type gives the
    wrong answer: a trailer sheet writes "GRAND JURY PRIZE FOR DIRECTION" and
    "CASCADIA INTERNATIONAL FILM FESTIVAL" only in capitals, so nothing in the
    trailer corpus shows they are names — the copying gate then counted both
    awards as the writer's own wording and asked it to paraphrase an award. The
    brand's press releases write them in running text, which settles it.

    Cached per business for the life of the process. New documents change the
    answer only by adding names, and a worker restart picks those up; being one
    upload behind costs a rebuild at worst, never a false accusation.
    """
    cached = _NAME_EVIDENCE_CACHE.get(business_id)
    if cached is not None:
        return cached

    try:
        from database import get_db_session, BrandDocument

        with get_db_session() as session:
            rows = (
                session.query(BrandDocument.file_content)
                .filter(BrandDocument.business_id == business_id)
                .filter(BrandDocument.file_content.isnot(None))
                .all()
            )
        evidence = "\n\n".join(row[0] for row in rows if row[0])
    except Exception as e:  # a missing evidence base must not fail enforcement
        logger.warning(
            "Could not load name evidence for business_id=%s: %s", business_id, e
        )
        evidence = ""

    _NAME_EVIDENCE_CACHE[business_id] = evidence
    return evidence


def brand_brain_is_usable(metrics: str) -> bool:
    """Whether this brand context can actually be scored against.

    Every reader in this codebase keys off the brain's headers — extract_section
    and the asset-bank readers on '#' blocks, brand_prose_section and
    measured_mechanics_section on the all-caps prose headers. A context with
    none of them is not a sparse brain, it is the absence of one, and nothing
    downstream degrades gracefully in that state: the writer falls through to
    its generic first-person-plural fallback, and every deterministic gate that
    reads a measured rate finds nothing to read and returns no failures.

    build_and_cache_context() returns "" when a (business_id, content_type) has
    no metric rows, which happens routinely — documents uploaded under a
    different content type, extraction still queued, a reset brand brain. So
    this is an ordinary state to be checked for, not an exceptional one.
    """
    if not metrics or not metrics.strip():
        return False
    return bool(re.search(r"^\s*#\s*\S", metrics, re.MULTILINE))
