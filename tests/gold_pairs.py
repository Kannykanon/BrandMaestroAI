"""Briefs paired with the writing they should have produced.

Every rubric change in this system has been justified by reading one bad run,
and more than one of them made the system worse in a way nothing caught: a
judge told to fix a draft's sentence lengths would have talked the hand-written
correct script out of the writer, because that script sits further from its own
corpus than the draft it was meant to replace.

So the corrections are scored against writing already judged good rather than
against a reading of the latest failure. A pair is a directory:

    tests/fixtures/gold/<name>/
        brief.txt              the product document the piece is written from
        gold.txt               the hand-written correct answer
        voice/*.txt            the brand-voice documents the Brain is built from
        rejected/<case>.txt    a draft that must be refused
        rejected/<case>.expect one word naming the gate that must refuse it

Adding a pair is dropping a directory in. Nothing here is registered by name.

The gates run without a model or a database, so the harness is free and runs on
every commit. What it cannot check is whether prose sounds right — that is the
voice pass's job, and it needs a model. What it can check is the property every
rubric change has threatened: that the correct answer still passes.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "gold")

# The gates a piece must clear. Each returns a list of human-readable failures.
GATES = ("mechanics", "preflight", "copying", "facts", "story")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


@dataclass
class Rejected:
    name: str
    content: str
    expect: str  # the gate that must catch it, from GATES


@dataclass
class Pair:
    name: str
    brief: str
    gold: str
    voice: list = field(default_factory=list)
    rejected: list = field(default_factory=list)

    def __str__(self) -> str:  # pytest ids
        return self.name

    @property
    def brain(self) -> str:
        """The measured half of the Brand Brain, built from this pair's corpus."""
        from brand_metrics import BrandMetricsSQL

        return BrandMetricsSQL.measure_documents(self.voice)


def discover() -> list:
    pairs = []
    for directory in sorted(glob.glob(os.path.join(FIXTURES, "*"))):
        if not os.path.isdir(directory):
            continue
        brief = os.path.join(directory, "brief.txt")
        gold = os.path.join(directory, "gold.txt")
        if not (os.path.exists(brief) and os.path.exists(gold)):
            continue
        rejected = []
        for draft in sorted(glob.glob(os.path.join(directory, "rejected", "*.txt"))):
            expect_path = draft[:-4] + ".expect"
            expect = _read(expect_path).strip() if os.path.exists(expect_path) else ""
            rejected.append(Rejected(os.path.basename(draft)[:-4], _read(draft), expect))
        pairs.append(Pair(
            name=os.path.basename(directory),
            brief=_read(brief),
            gold=_read(gold),
            voice=[_read(p) for p in sorted(glob.glob(os.path.join(directory, "voice", "*.txt")))],
            rejected=rejected,
        ))
    return pairs


def failures(content: str, pair: Pair) -> dict:
    """Every deterministic gate that would refuse this content, by gate name.

    The same checks the enforcer runs, in the same order, minus the ones that
    need a model. Kept in one place so a change to a gate is scored against
    every pair at once.
    """
    from utils.coverage import dropped_detail
    from utils.enforcement import check_measured_mechanics, run_preflight_checks
    from utils.enforcement.constants import NARRATIVE_VERBATIM_SPAN_WORDS
    from utils.enforcement.provenance import find_extractive_spans
    from utils.fact_spans import extract_fact_spans, missing_fact_spans
    from utils.voice_spec import band_high

    brain = pair.brain
    found: dict = {gate: [] for gate in GATES}
    found["mechanics"] = [f["message"] for f in check_measured_mechanics(content, brain)]
    found["preflight"] = [f["message"] for f in run_preflight_checks(content, brain)]
    found["copying"] = [
        f"{span['length']} words: {span['text'][:60]}"
        for span in find_extractive_spans(content, pair.brief,
                                          max_span=NARRATIVE_VERBATIM_SPAN_WORDS)
    ]
    found["facts"] = [f'lost: "{span}"'
                      for span in missing_fact_spans(content, extract_fact_spans(pair.brief))]
    found["story"] = [
        f"{'never told' if beat['skipped'] else 'told thinly'}: {beat['beat'][:60]}"
        for beat in dropped_detail(content, pair.brief,
                                   abstraction_high=band_high(brain, "nominalisations_per_100_words"))
    ]
    return {gate: items for gate, items in found.items() if items}
