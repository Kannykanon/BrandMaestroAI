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
from utils.brand_profile import (
    extract_asset_bank,
    extract_brand_name,
    extract_section,
)

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
    generation_instructions = extract_section(metrics, "GENERATION INSTRUCTIONS")
    signature_phrases       = extract_section(metrics, "SIGNATURE CONSTRUCTIONS")
    brand_name              = extract_brand_name(metrics)
    
    # Formulaic patterns
    opening_formula         = extract_section(metrics, "OPENING PATTERN")
    closing_formula         = extract_section(metrics, "CLOSING PATTERN")
    mechanical_rules        = extract_section(metrics, "MECHANICAL RULES")
    evidence_anchoring      = extract_section(metrics, "EVIDENCE PATTERN")
    diagnostic_style        = extract_section(metrics, "DIAGNOSTIC STYLE")
    reframing_moves         = extract_section(metrics, "REFRAMING MOVES")
    
    # Structural patterns — combine signature constructions + thinking templates
    # for a complete picture of the brand's canonical structural moves
    structural_patterns = extract_section(metrics, "SIGNATURE CONSTRUCTIONS")
    thinking_templates  = extract_section(metrics, "THINKING TEMPLATES")
    canonical_formula   = extract_section(metrics, "CANONICAL STRUCTURAL FORMULA")
    if thinking_templates:
        structural_patterns = (structural_patterns + "\n\nTHINKING TEMPLATES:\n" + thinking_templates) if structural_patterns else thinking_templates
    if canonical_formula:
        structural_patterns = (structural_patterns + "\n\nCANONICAL STRUCTURAL FORMULA:\n" + canonical_formula) if structural_patterns else canonical_formula
    
    # New dimensions from improved extraction
    measured_mechanics      = extract_section(metrics, "MEASURED MECHANICS")
    pronoun_pattern         = extract_section(metrics, "PRONOUN PATTERN")
    qualification_style     = extract_section(metrics, "QUALIFICATION STYLE")
    tone_signature          = extract_section(metrics, "TONE SIGNATURE")
    capitalization_style    = extract_section(metrics, "CAPITALIZATION STYLE")

    # Extract permitted claims asset bank — passed explicitly to prevent hallucination
    asset_bank = extract_asset_bank(metrics)

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
        # Gates already corrected in earlier rounds. Handed to the editor as
        # standing constraints, because it only ever sees the newest feedback:
        # without them it fixes the latest violation and reintroduces one it had
        # already resolved, and the loop oscillates until it runs out of
        # iterations on a draft that was acceptable two rounds earlier.
        prior_violations = state.get("violation_history") or []
        if prior_violations:
            listed = "\n".join(f"  - {v}" for v in prior_violations)
            standing_constraints = (
                "STANDING CONSTRAINTS — already corrected once, do not "
                f"reintroduce:\n{listed}"
            )
        else:
            standing_constraints = "STANDING CONSTRAINTS: none yet."

        # REVISION: Only run the Editor (WRITER_REVISION) to fix feedback
        prompt = WRITER_REVISION.format(
            standing_constraints=standing_constraints,
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
