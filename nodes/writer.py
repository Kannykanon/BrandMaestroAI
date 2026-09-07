# nodes/writer.py
import logging
from model import LLMSingleton
from brand_rag import BrandRAG
from learning_memory import FeedbackPortSQL
from brand_metrics import BrandMetricsSQL
from prompts.writer import (
    WRITER_PLANNER,
    WRITER_DRAFTER,
    WRITER_EDITOR,
    WRITER_REVISION,
    HUMAN_DIRECTIVE_BLOCK,
)
from graph.state import GraphState

logger = logging.getLogger(__name__)
import re
from utils.observe import observe




DEFAULT_METRICS = {
    "tone": "warm, conversational",
    "perspective": "first-person plural (we/our)",
    "style": "short punchy sentences",
    "avoid": ["passive voice", "corporate jargon"]
}

GENERIC_EXAMPLES = """
Example 1: Community-focused storytelling
"We believe every customer should feel valued. Here's how we do it..."

Example 2: Benefit-led messaging
"Looking for sustainable fashion? Our new collection is here..."
"""

DEFAULT_PATTERNS = {
    "approved": [],
    "rejected": []
}


def _extract_section(text: str, header: str) -> str:
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



def _extract_brand_name(metrics: str) -> str:
    """
    Extract the brand name, tolerating a common synthesis-LLM slip where the
    name is folded straight into the section header (e.g. '# MERIDIAN STUDIOS')
    instead of the requested '# BRAND NAME' header followed by the name on its
    own line. Without this fallback, that formatting drift silently defaults
    the writer to a generic 'our agency' placeholder in the generated copy.
    """
    name = _extract_section(metrics, "BRAND NAME")
    if name and "not extracted" not in name.lower() and "inject manually" not in name.lower():
        return name.strip()

    first_header = re.match(r"\s*#\s*(.+)", metrics)
    if first_header:
        candidate = first_header.group(1).strip()
        if candidate and candidate.upper() not in ("BRAND NAME", "BRAND ASSET BANK"):
            return candidate.title() if candidate.isupper() else candidate

    return ""


def _extract_asset_bank(metrics: str) -> str:
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



@observe("writer_node")
def writer_node(state: GraphState) -> GraphState:
    """
    Generates or revises brand-consistent content.

    `rag`, `analyzer`, and `memory` are resolved per call from
    state["business_id"] / state["content_type"] (see graph/deps.py)
    rather than pre-bound, so this node is compatible with a graph
    compiled once and reused across requests (Agent Platform Runtime).
    """
    from graph.deps import resolve_deps
    rag, analyzer, memory = resolve_deps(state["business_id"], state["content_type"])

    topic        = state["topic"]
    content_type = state["content_type"]
    format_type  = state.get("format_type", "") or content_type
    research     = state["research"]
    feedback     = state.get("feedback", "")
    iteration    = state.get("iteration", 0) + 1

    # A human reviewer's rejection note, carried in from human_loop. Rendered
    # once here and injected into whichever prompt this pass uses, so the
    # instruction survives both the fresh planner/drafter/editor path and any
    # later enforcer-driven revision rounds within the same run.
    human_feedback = (state.get("human_feedback") or "").strip()
    human_directive = (
        HUMAN_DIRECTIVE_BLOCK.format(human_feedback=human_feedback)
        if human_feedback else ""
    )
    if human_feedback:
        logger.info(
            "Writer applying reviewer directive (%d chars) at iteration %d",
            len(human_feedback), iteration,
        )

    try:
        metrics = analyzer.get_context()
    except Exception as e:
        logger.warning("Metrics analyzer failed, using defaults: %s", e)
        metrics = str(DEFAULT_METRICS)

    # Extract high-signal sections from brand brain for focused injection
    # These are injected individually — we do NOT also inject the full metrics blob
    # to avoid duplicate context that wastes tokens and confuses the model
    generation_instructions = _extract_section(metrics, "GENERATION INSTRUCTIONS")
    signature_phrases       = _extract_section(metrics, "SIGNATURE CONSTRUCTIONS")
    brand_name              = _extract_brand_name(metrics)
    
    # Formulaic patterns
    opening_formula         = _extract_section(metrics, "OPENING PATTERN")
    closing_formula         = _extract_section(metrics, "CLOSING PATTERN")
    mechanical_rules        = _extract_section(metrics, "MECHANICAL RULES")
    evidence_anchoring      = _extract_section(metrics, "EVIDENCE PATTERN")
    diagnostic_style        = _extract_section(metrics, "DIAGNOSTIC STYLE")
    reframing_moves         = _extract_section(metrics, "REFRAMING MOVES")
    
    # Structural patterns — combine signature constructions + thinking templates
    # for a complete picture of the brand's canonical structural moves
    structural_patterns = _extract_section(metrics, "SIGNATURE CONSTRUCTIONS")
    thinking_templates  = _extract_section(metrics, "THINKING TEMPLATES")
    canonical_formula   = _extract_section(metrics, "CANONICAL STRUCTURAL FORMULA")
    if thinking_templates:
        structural_patterns = (structural_patterns + "\n\nTHINKING TEMPLATES:\n" + thinking_templates) if structural_patterns else thinking_templates
    if canonical_formula:
        structural_patterns = (structural_patterns + "\n\nCANONICAL STRUCTURAL FORMULA:\n" + canonical_formula) if structural_patterns else canonical_formula
    
    # New dimensions from improved extraction
    measured_mechanics      = _extract_section(metrics, "MEASURED MECHANICS")
    pronoun_pattern         = _extract_section(metrics, "PRONOUN PATTERN")
    qualification_style     = _extract_section(metrics, "QUALIFICATION STYLE")
    tone_signature          = _extract_section(metrics, "TONE SIGNATURE")
    capitalization_style    = _extract_section(metrics, "CAPITALIZATION STYLE")

    # Extract permitted claims asset bank — passed explicitly to prevent hallucination
    asset_bank = _extract_asset_bank(metrics)

    # Fall back gracefully if sections are missing (cold start / sparse brain)
    if not generation_instructions:
        generation_instructions = "Write in first-person plural (we/our). Be direct and authoritative. Ground claims in specific experience and data."
    if not signature_phrases:
        signature_phrases = "None extracted yet — rely on brand voice metrics above."
    if not brand_name:
        brand_name = "our agency"
    
    # Fallbacks for formulaic patterns
    if not opening_formula:
        opening_formula = "No specific opening formula extracted. Write a natural, engaging opening appropriate for the topic."
    if not closing_formula:
        closing_formula = "No specific closing formula extracted. Write a natural closing appropriate for the topic."
    if not mechanical_rules:
        mechanical_rules = "Use standard paragraph structure."
    if not evidence_anchoring:
        evidence_anchoring = "Ground claims in specific numbers, timeframes, or client outcomes."
    if not diagnostic_style:
        diagnostic_style = "No specific diagnostic style extracted. Present problems clearly."
    if not reframing_moves:
        reframing_moves = "No specific reframing moves extracted. Explain concepts straightforwardly."
    if not pronoun_pattern:
        pronoun_pattern = "Use the appropriate pronoun perspective for the content type."
    if not qualification_style:
        qualification_style = "State claims clearly and confidently."
    if not tone_signature:
        tone_signature = "Professional and natural."
    if not capitalization_style:
        capitalization_style = (
            "No capitalization convention extracted yet — use standard sentence "
            "case unless the STRUCTURAL EXAMPLES clearly show otherwise."
        )
    if not structural_patterns:
        structural_patterns = "No structural patterns extracted yet — follow the opening/closing formulas and generation instructions above."

    # Structural RAG — fetch examples of structural elements
    try:
        structural_examples = rag.query_structure("opening or closing", topic)
        if not structural_examples:
            structural_examples = GENERIC_EXAMPLES
    except Exception as e:
        logger.warning("Structural RAG failed, using generic examples: %s", e)
        structural_examples = GENERIC_EXAMPLES

    # Learning memory — approved/rejected patterns with feedback reasoning
    try:
        patterns = memory.get_patterns(content_type)
    except Exception as e:
        logger.warning("Memory failed, using empty patterns: %s", e)
        patterns = DEFAULT_PATTERNS

    # Include feedback reasoning alongside angle labels (#14)
    approved_entries = patterns.get("approved", [])[:3]
    rejected_entries = patterns.get("rejected", [])[:2]
    
    approved_str = "\n".join(
        f"- {p['angle']}" + (f" (feedback: {p['feedback']})" if p.get('feedback') else "")
        for p in approved_entries
    ) if approved_entries else "None yet"
    
    rejected_str = "\n".join(
        f"- {p['angle']}" + (f" (reason: {p['feedback']})" if p.get('feedback') else "")
        for p in rejected_entries
    ) if rejected_entries else "None yet"

    if iteration == 1:
        # STEP 1: PLANNER
        prompt_planner = WRITER_PLANNER.format(
            human_directive=human_directive,
            topic=topic,
            content_type=content_type,
            format_type=format_type,
            research=research or "No research available — plan generically around the topic string above.",
            structural_examples=structural_examples,
            opening_formula=opening_formula,
            closing_formula=closing_formula,
            structural_patterns=structural_patterns
        )
        try:
            outline_result = LLMSingleton.get().invoke(prompt_planner)
            outline = outline_result.content
            logger.info("Planner generated outline successfully.")
        except Exception as e:
            logger.error("Planner LLM failed: %s", e)
            outline = "[System Error: Unable to generate outline]"

        # STEP 2: DRAFTER
        prompt_drafter = WRITER_DRAFTER.format(
            human_directive=human_directive,
            topic=topic,
            content_type=content_type,
            format_type=format_type,
            outline=outline,
            brand_name=brand_name,
            asset_bank=asset_bank,
            research=research,
            structural_examples=structural_examples,
            capitalization_style=capitalization_style
        )
        try:
            draft_result = LLMSingleton.get().invoke(prompt_drafter)
            draft = draft_result.content
            logger.info("Drafter generated draft successfully.")
        except Exception as e:
            logger.error("Drafter LLM failed: %s", e)
            draft = "[System Error: Unable to generate draft]"

        # STEP 3: EDITOR
        prompt_editor = WRITER_EDITOR.format(
            human_directive=human_directive,
            measured_mechanics=measured_mechanics or "No measured mechanics available.",
            draft=draft,
            format_type=format_type,
            generation_instructions=generation_instructions,
            mechanical_rules=mechanical_rules,
            evidence_anchoring=evidence_anchoring,
            diagnostic_style=diagnostic_style,
            reframing_moves=reframing_moves,
            signature_phrases=signature_phrases,
            pronoun_pattern=pronoun_pattern,
            qualification_style=qualification_style,
            tone_signature=tone_signature,
            capitalization_style=capitalization_style
        )
        try:
            editor_result = LLMSingleton.get().invoke(prompt_editor)
            content = editor_result.content
            logger.info("Editor finalized content successfully.")
        except Exception as e:
            logger.error("Editor LLM failed: %s", e)
            content = f"[System Error: Unable to finalize content - {str(e)[:80]}]"

    else:
        # REVISION: Only run the Editor (WRITER_REVISION) to fix feedback
        prompt = WRITER_REVISION.format(
            human_directive=human_directive,
            previous_content=state.get("content", ""),
            format_type=format_type,
            feedback=feedback,
            flagged_passages=state.get("flagged_passages", "No specific passages flagged."),
            style_match=state.get("style_match", 0.0),
            tone_match=state.get("tone_match", 0.0),
            structure_match=state.get("structure_match", 0.0),
            signature_match=state.get("signature_match", 0.0),
            generation_instructions=generation_instructions,
            brand_name=brand_name,
            opening_formula=opening_formula,
            closing_formula=closing_formula,
            mechanical_rules=mechanical_rules,
            evidence_anchoring=evidence_anchoring,
            diagnostic_style=diagnostic_style,
            reframing_moves=reframing_moves,
            signature_phrases=signature_phrases,
            pronoun_pattern=pronoun_pattern,
            qualification_style=qualification_style,
            tone_signature=tone_signature,
            capitalization_style=capitalization_style,
            asset_bank=asset_bank
        )
        try:
            result = LLMSingleton.get().invoke(prompt)
            content = result.content
        except Exception as e:
            logger.error("Revision LLM failed: %s", e)
            content = f"[System Error: Unable to revise content - {str(e)[:80]}]"

    logger.info("Writer iteration=%d complete for topic=%r", iteration, topic)

    return {
        **state,
        "content": content,
        "iteration": iteration
    }