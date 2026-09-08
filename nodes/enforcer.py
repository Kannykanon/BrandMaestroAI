# nodes/enforcer.py
"""The Enforcer: scores a draft against the Brand Brain and gates what ships.

Deterministic checks live in utils.enforcement — they are the rules that can be
decided by inspection once the brand's own habits are known, so they run before
any model call and their verdicts are re-applied in code afterwards rather than
trusted to the model's own compliance.
"""
import logging

from graph.state import GraphState
from model import LLMSingleton
from prompts.enforcer import ENFORCER_HUMAN_DIRECTIVE, ENFORCER_PROMPT
from utils.brand_profile import extract_permitted_claims
from utils.enforcement import (
    MAX_ITERATIONS,
    MAX_VERBATIM_SPAN_WORDS,
    find_altered_quotations,
    find_extractive_spans,
    find_ungrounded_contact_details,
    find_unverified_quote_attributions,
    run_preflight_checks,
    sanitize_banned_punctuation,
)
from utils.llm_output import parse_llm_json
from utils.observe import observe

logger = logging.getLogger(__name__)


@observe("enforcer_node")
def enforcer_node(state: GraphState) -> GraphState:
    from graph.deps import resolve_deps
    rag, analyzer, _memory = resolve_deps(state["business_id"], state["content_type"])

    # Gates this draft has already been corrected for. The writer sees only
    # the newest feedback, so without carrying this forward it fixes the latest
    # violation and reintroduces one it had already resolved — the loop then
    # oscillates instead of converging and runs out of iterations on a draft
    # that was acceptable two rounds earlier.
    prior_violations = list(state.get("violation_history") or [])

    def with_violation(label: str) -> list[str]:
        return prior_violations if label in prior_violations else prior_violations + [label]

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
    content, punctuation_fixes = sanitize_banned_punctuation(content, metrics)
    if punctuation_fixes:
        logger.info(
            "Auto-sanitized banned punctuation at iteration %d: %s",
            iteration, punctuation_fixes,
        )
    state = {**state, "content": content}

    # Build permitted claims whitelist from asset bank
    permitted_claims = extract_permitted_claims(metrics)

    # 1a. Fabricated-attribution gate — a quote attributed to a named person
    # who appears nowhere in this generation's actual source material (the
    # brand brief or the Researcher's output) is a hallucination the numeric
    # claims check below cannot see, since it isn't a statistic. Checked
    # deterministically so it cannot be reasoned around the way an LLM-only
    # check was on the very first version of this evaluator.
    grounding_text = f"{metrics}\n\n{state.get('topic', '')}\n\n{state.get('research', '')}"
    fabricated_attributions = find_unverified_quote_attributions(content, grounding_text)
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
            "violation_history": with_violation("fabricated quote attribution"),
            "creative_angle": "unknown",
        }

    # 1a-1. Fabricated contact details. Grouped with the attribution gate
    # because it is the same class of failure — inventing a verifiable-looking
    # real-world detail — and it must never ship, so it returns early rather
    # than becoming a score deduction that max-iteration approval can wave past.
    ungrounded_contacts = find_ungrounded_contact_details(content, grounding_text)
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
            "violation_history": with_violation("fabricated contact details"),
            "creative_angle": "unknown",
        }

    # 1a-1b. Altered-quotation gate. Same class again: a verifiable real-world
    # detail changed. Inventing a quote is caught above; this catches the
    # subtler version, where a real quote is restyled into the brand's voice
    # while still sitting inside quotation marks and attributed to a real
    # person. Observed in social copy, which turned a sound designer's "It
    # isn't. It's loud, and it's close" into "It is not. It is loud. It is
    # close." Returns early: a misquotation is a factual error about a named
    # human being, not a style deduction to be waved past at max iterations.
    altered_quotes = find_altered_quotations(content, grounding_text)
    if altered_quotes:
        logger.warning(
            "ALTERED QUOTATION(S) at iteration %d: %s",
            iteration, [q["quoted"][:60] for q in altered_quotes],
        )
        feedback = (
            "ALTERED QUOTATION — this draft changes words inside quotation marks. "
            "A quotation is a claim about what somebody actually said, so it is "
            "reproduced exactly or not at all. Brand voice applies to your own "
            "prose, never to the words of the people you are quoting:\n"
        )
        flagged_lines = []
        for q in altered_quotes:
            feedback += (
                f"  - you wrote: \"{q['quoted']}\"\n"
                f"    the source says: \"{q['source']}\"\n"
            )
            flagged_lines.append(
                f"\"{q['quoted']}\" — altered quotation; the source says \"{q['source']}\""
            )
        feedback += (
            "\nRestore the source wording exactly, including its contractions and "
            "punctuation. If you want a shorter quote, cut it at a word boundary "
            "and keep what remains verbatim. If you want the point in the brand's "
            "own voice, take it out of quotation marks and out of the "
            "attribution, and state it as the brand's own sentence."
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
            "violation_history": with_violation("altered quotation"),
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
    extractive_spans = find_extractive_spans(content, research_text)
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
            "violation_history": with_violation("extractive copying"),
            "creative_angle": "unknown",
        }

    # 1b. Run Fast Pre-flight Checks (Bypass LLM if failed)
    preflight_failures = run_preflight_checks(content, metrics)
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
            "violation_history": with_violation("mechanical rule violation"),
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
        evaluation = parse_llm_json(result.content)
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
            evaluation = parse_llm_json(repair.content)
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
