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
    asset_text = match.group(1).strip()

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


def extract_permitted_claims(metrics: str) -> str:
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
