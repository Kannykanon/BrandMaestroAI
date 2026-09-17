"""Ask the voice pass to score writing whose quality is already settled.

Everything else in this repository checks the rules. This checks the judge.

A run scored one draft 6.5 five times for reading as "a monotonous list of
facts", then scored a modestly revised version 9.8 and approved it. Measured,
that draft's sentence rhythm was indistinguishable from the hand-written gold's
— median 6 words against 5, the same spread, the same commonest length — so the
complaint was mostly wrong and so was the praise. A judge that noisy cannot be
fixed by rewriting its brief, which is the only thing prompt edits can do.

What settles it is asking the judge to score the answer already known to be
right. If the gold scores well and the known-bad drafts score below it, the
rubric is calibrated and the noise is tolerable. If the gold is lectured about
its rhythm, the rubric is wrong about this brand and no edit to the wording of
the brief will help — the fix is to anchor the judge with the gold itself.

This costs one model call per draft and is never run by the test suite. Run it
after changing the judge's brief, the voice spec, or the model:

    python -m scripts.calibrate_judge                 # every pair
    python -m scripts.calibrate_judge kancity         # one pair

Exit status is 0 when every pair is calibrated, 1 when one is not, so it can
gate a deploy if that is ever wanted.
"""
from __future__ import annotations

import sys

# The score below which the enforcer refuses a draft outright (nodes/enforcer.py).
MIN_VOICE_SCORE = 6.0

# What the gold has to score for the rubric to be considered calibrated. It is
# the answer this brand's own reviewer wrote; a judge that will not give it an
# 8 is not measuring this brand.
GOLD_EXPECTED = 8.0


def judge(content: str, pair, topic: str = "") -> dict:
    """Score one draft with the real voice pass, in the real prompt."""
    from model import LLMSingleton
    from prompts.enforcer import ENFORCER_PROMPT
    from utils.brand_profile import extract_permitted_claims, extract_section, strip_invented_counts
    from utils.llm_output import parse_llm_json

    brain = pair.brain
    craft = [extract_section(brain, name) for name in
             ("OPENING PATTERN", "CLOSING PATTERN", "SIGNATURE CONSTRUCTIONS", "STRUCTURAL PATTERNS")]
    prompt = ENFORCER_PROMPT.format(
        metrics=brain,
        content=content,
        topic=topic or pair.name,
        research=pair.brief,
        permitted_claims=extract_permitted_claims(brain, "script"),
        human_directive="",
        voice_spec=extract_section(brain, "VOICE SPEC") or "No voice spec measured for this brand yet.",
        voice_craft=strip_invented_counts("\n\n".join(p for p in craft if p))
        or "Nothing extracted yet — judge against the writing habits above.",
        closed_subjects="- Nothing yet; this is the first voice pass on this draft.",
    )
    raw = LLMSingleton.get("enforcement").invoke(prompt)
    evaluation = parse_llm_json(getattr(raw, "content", raw)) or {}
    return {
        "voice_score": float(evaluation.get("voice_score", 0.0) or 0.0),
        "score": float(evaluation.get("score", 0.0) or 0.0),
        "subject": str(evaluation.get("voice_subject") or "").strip(),
        "rewrites": evaluation.get("voice_rewrites") or [],
    }


def verdict(gold_score: float, worst_rejected: float, had_rejected: bool) -> tuple:
    """Whether the judge is calibrated for this pair, and why not if it is not.

    Two questions, in order. Does the judge rate this brand's own correct answer
    the way a reader did? And does it rate the drafts that shipped wrongly below
    that? A rubric can fail either — one that likes everything is as useless as
    one that likes nothing.
    """
    if gold_score < MIN_VOICE_SCORE:
        return False, (
            f"MISCALIBRATED: the judge would REFUSE this brand's own correct answer "
            f"({gold_score:.1f} is below the {MIN_VOICE_SCORE:.0f} the enforcer blocks at). "
            f"No wording change to the brief fixes this; anchor the judge with the gold."
        )
    if gold_score < GOLD_EXPECTED:
        return False, (
            f"MISCALIBRATED: the gold scores {gold_score:.1f}, under the {GOLD_EXPECTED:.0f} a "
            f"known-right answer should reach. The rubric is measuring something this brand does not do."
        )
    if had_rejected and worst_rejected >= gold_score:
        return False, (
            f"MISCALIBRATED: a draft that shipped wrongly scored {worst_rejected:.1f}, "
            f"at or above the gold's {gold_score:.1f}."
        )
    return True, "calibrated: the gold scores well and beats every draft that shipped wrongly."


def report(pair) -> bool:
    print(f"\n=== {pair.name} ===")
    gold = judge(pair.gold, pair)
    print(f"  gold{'':24} voice {gold['voice_score']:.1f}  overall {gold['score']:.1f}")
    if gold["rewrites"]:
        first = gold["rewrites"][0]
        if isinstance(first, dict):
            print(f"      it would rewrite: {str(first.get('from'))[:70]!r}")
            print(f"                    as: {str(first.get('to'))[:70]!r}")
    if gold["subject"]:
        print(f"      it faults the gold for: {gold['subject']}")

    worst = 0.0
    for draft in pair.rejected:
        result = judge(draft.content, pair)
        worst = max(worst, result["voice_score"])
        print(f"  {draft.name:28} voice {result['voice_score']:.1f}  overall {result['score']:.1f}")

    calibrated, why = verdict(gold["voice_score"], worst, bool(pair.rejected))
    print(f"  -> {why}")
    return calibrated


def main(argv: list) -> int:
    from tests.gold_pairs import discover

    wanted = set(argv)
    pairs = [p for p in discover() if not wanted or p.name in wanted]
    if not pairs:
        print("No gold pairs found.", file=sys.stderr)
        return 1

    print("Scoring writing whose quality is already settled. One model call per draft.")
    return 0 if all([report(pair) for pair in pairs]) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
