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
from utils.brand_profile import (
    brand_brain_is_usable,
    brand_name_evidence,
    extract_permitted_claims,
)
from utils.enforcement import (
    MAX_ITERATIONS,
    MAX_VERBATIM_SPAN_WORDS,
    find_altered_quotations,
    find_extractive_spans,
    find_unbranded_emphasis_caps,
    source_quotation_for_span,
    find_ungrounded_contact_details,
    find_unverified_quote_attributions,
    run_preflight_checks,
    sanitize_banned_punctuation,
    sanitize_unbranded_emphasis_caps,
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

    def with_violation(label: str, detail: str = "") -> list[str]:
        # The detail matters for a repeat offender. Social copy was told
        # "extractive copying" at rounds one, three, five and six and reached for
        # the same distinctive sentence every time; a bare gate name does not say
        # which phrase to stop using, so the constraint could not be acted on.
        entry = f"{label}: {detail}" if detail else label
        return prior_violations if entry in prior_violations else prior_violations + [entry]

    content        = state["content"]
    metrics        = analyzer.get_context()
    iteration      = state.get("iteration", 1)
    max_iterations = MAX_ITERATIONS

    # -1. No Brand Brain, no verdict.
    #
    # Checked in code, ahead of everything else, for the same reason the
    # hallucination and publishability verdicts are re-applied in code below:
    # an APPROVAL RULE is not an enforcement mechanism. Asked to score a draft
    # against an empty brand context, the model said so and returned 1.0 twice —
    # and then, on the identical empty input, returned 8.8 and approved. The
    # scoring model is the wrong place for a structural precondition, because
    # its answer to one is a sample rather than a decision.
    #
    # Nothing below this line works without a brain either. The writer has
    # already fallen through to its generic "first-person plural, be direct"
    # fallback, so the draft is written in the system's default voice rather
    # than the brand's; run_preflight_checks finds no PUNCTUATION HABITS and
    # checks nothing; check_measured_mechanics finds no MEASURED MECHANICS and
    # returns no failures; and find_unfilled_placeholders has no measured
    # bracket rate, so it cannot tell a template slot from a house convention
    # and stays quiet. Observed live: a trailer shipped at 8.8/10 containing a
    # literal "[Movie Title]", written at 2.7x the brand's own four-syllable
    # rate and with none of its contractions, because all four gates were off
    # at once and the only thing still running was a model with nothing to
    # compare against.
    #
    # This is not a content defect, so it is not sent back to the writer: no
    # revision can supply a missing brain. The iteration counter is advanced to
    # the ceiling so the graph routes straight to the deployer and the run ends
    # with an honest score instead of spending two more rounds of LLM calls on
    # a state the writer cannot change. It records approved=False and 0.0, which
    # the max-iteration force-approval below cannot reach, because that only
    # ever rescues a quality shortfall.
    if not brand_brain_is_usable(metrics):
        logger.error(
            "NO BRAND BRAIN for business_id=%s content_type=%s — refusing to "
            "score. The brand context is empty, so every deterministic gate is "
            "inert and the draft was written from the writer's generic "
            "fallback, not this brand's voice.",
            state.get("business_id"), state.get("content_type"),
        )
        return {
            **state,
            "approved": False,
            "score": 0.0,
            "style_match": 0.0,
            "tone_match": 0.0,
            "structure_match": 0.0,
            "signature_match": 0.0,
            "feedback": (
                "NO BRAND BRAIN — there is no voice profile for "
                f"content type '{state.get('content_type')}', so this draft "
                "cannot be scored against the brand and was not written from "
                "it either. This is a setup problem, not a writing problem, and no "
                "revision will fix it. Upload this brand's previously "
                "successful content for THIS content type with doc_role=voice, "
                "wait for extraction and synthesis to finish, then generate "
                "again. A brain is built per (business, content type): "
                "documents uploaded under a different content type do not "
                "carry over."
            ),
            "flagged_passages": "No specific passages flagged.",
            "violation_history": with_violation("no brand brain"),
            "creative_angle": "unknown",
            "iteration": max_iterations,
        }

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

    # 0b. Same treatment for capitals the brand does not shout. Bouncing the
    # draft did not work: social copy was corrected at round two for EXCLUSIVE,
    # GRAND, NOT and UNIQUE, reached 7.9 by round five, then shouted PERCEPTION,
    # NOT, UNIQUE and NOW at round six — a fresh set each time, with the
    # constraint listed in front of it. The writer was not reintroducing the same
    # violation, so telling it what it had already fixed could not help. Lower
    # casing a word is mechanical, so it gets a mechanical fix.
    #
    # grounding_text is built below; the brand's own documents are what say which
    # capitals are permitted, so this needs the same text.
    _grounding_for_caps = "\n\n".join([metrics, state.get("research", "") or ""])
    content, caps_fixes = sanitize_unbranded_emphasis_caps(content, _grounding_for_caps)
    if caps_fixes:
        logger.info(
            "Auto-sanitized unbranded emphasis capitals at iteration %d: %s",
            iteration, caps_fixes,
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

    # 1a-1d. Emphasis capitals the brand does not use. The measured all-caps
    # rate cannot see this: a social draft that shouted EXCLUSIVE, FIRST,
    # ACCLAIMED and UNIQUE measured 5.9 per 100 words against the brand's 4.4 —
    # inside tolerance, and approved. The count was right and every choice was
    # wrong. The brand's capitals are its title and its card lines, never an
    # adjective it wants to lean on, so the question is which words rather than
    # how many.
    emphasis_caps = find_unbranded_emphasis_caps(content, grounding_text)
    if emphasis_caps:
        shouted = [c["word"] for c in emphasis_caps]
        logger.warning(
            "UNBRANDED EMPHASIS CAPITALS at iteration %d: %s", iteration, shouted,
        )
        feedback = (
            "EMPHASIS CAPITALS THE BRAND DOES NOT USE — this draft sets words in "
            "capitals mid-sentence that the brand never capitalises anywhere in its "
            "own material:\n"
        )
        for word in shouted:
            feedback += f"  - {word}\n"
        feedback += (
            "\nWrite them in normal case. The brand's capitals belong to its title and "
            "to lines that stand on their own, not to adjectives you want to stress. "
            "If a point needs emphasis, get it from what the sentence says."
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
            "flagged_passages": "\n".join(
                f"\"{w}\" — capitalised for emphasis; the brand does not do this" for w in shouted
            ),
            "violation_history": with_violation("unbranded emphasis capitals"),
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
    # Which words are names is a property of the brand, so the evidence is
    # every document this business has uploaded — not this content type's
    # research, which for trailer copy contains the award names only in
    # capitals and so shows nothing about them being names at all.
    name_evidence = brand_name_evidence(state["business_id"]) + "\n\n" + grounding_text
    extractive_spans = find_extractive_spans(
        content, research_text, name_evidence=name_evidence
    )
    if extractive_spans:
        logger.warning(
            "EXTRACTIVE COPYING at iteration %d — %d span(s), longest %d words",
            iteration, len(extractive_spans), extractive_spans[0]["length"],
        )
        # A copied span that is a source quotation needs the opposite
        # instruction from a copied span of prose. Reproducing somebody's words
        # exactly is correct; doing it without quotation marks is what makes it
        # a defect, and the remedy is punctuation.
        #
        # Told only to "keep the fact, discard the wording", the writer
        # paraphrased the quotation into quotation marks — at which point the
        # hallucination check rejected it for not matching the source, so the
        # next round restored the wording and dropped the marks again. The
        # press-release path spent all six rounds alternating between those two
        # states and ended unapproved, because the one resolution that satisfies
        # both gates was the thing this feedback forbade.
        quoted_spans, prose_spans = [], []
        for span in extractive_spans:
            source_quote = source_quotation_for_span(span["text"], research_text)
            (quoted_spans if source_quote else prose_spans).append((span, source_quote))

        feedback = (
            "EXTRACTIVE COPYING DETECTED — this draft reproduces its source material "
            f"verbatim. Passages of more than {MAX_VERBATIM_SPAN_WORDS} consecutive "
            "words are copied from the research:\n"
        )
        flagged_lines = []

        if quoted_spans:
            feedback += (
                "\nThese passages are QUOTATIONS from the source, reproduced without "
                "quotation marks. Do NOT reword them — a quotation has to match what "
                "the person said. Put each one back inside quotation marks and attribute "
                "it to the speaker the source attributes it to, or remove it entirely:\n"
            )
            for span, source_quote in quoted_spans:
                feedback += f"  - ({span['length']} words) \"{span['text']}\"\n"
                feedback += f"    the source quotes this as: \"{source_quote}\"\n"
                flagged_lines.append(
                    f"\"{span['text']}\" — a source quotation reproduced without "
                    f"quotation marks; restore the marks and the attribution"
                )

        if prose_spans:
            feedback += (
                "\nThese passages are the source's own prose, not anybody's quoted "
                "words:\n"
            )
            for span, _ in prose_spans:
                feedback += f"  - ({span['length']} words) \"{span['text']}\"\n"
                flagged_lines.append(
                    f"\"{span['text']}\" — {span['length']} consecutive words copied from source"
                )
            feedback += (
                "\nRewrite each of those: keep the FACT, discard the source's wording and "
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
            "violation_history": with_violation(
                "do not reuse this wording from the source",
                "; ".join(
                    '"' + span["text"][:90] + '"' for span, _ in prose_spans
                ) or None,
            ),
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
