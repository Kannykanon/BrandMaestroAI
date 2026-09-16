"""The generation that started this, run against the gates as a fixture.

Four screenplay retellings were uploaded as brand voice and a story treatment as
the product document. What came back was this:

    He observes a congregation. Numerous individuals are present. They exceed seven.
    ...
    EMK extends his numerical contact. He advises a call for any impending difficulty.

It scored 6.8 and was force-approved. The hand-written correct version of the
same scene reads:

    More than seven men. Seated. Drinking.

Every file here is the real one: `voice/` holds the four documents the Brain was
built from, `treatment.txt` the product document, `rejected_draft.txt` what the
system produced, and `gold_script.txt` what it should have produced.

The harness is deliberately narrow. It runs only the gates that are
deterministic, so it needs no model and no database, and it asserts the property
that was violated: the correct version passes every blocking gate, and the bad
one does not. A model has to judge whether prose sounds like a brand; nothing
here pretends otherwise. What it can prove is that the deterministic layer no
longer stands in the way of the right answer — which is what it was doing when
it told this draft to reword "more than seven men".
"""
import glob
import os

import pytest

from brand_metrics import BrandMetricsSQL
from utils.enforcement import check_measured_mechanics, run_preflight_checks
from utils.enforcement.constants import MAX_VERBATIM_SPAN_WORDS, NARRATIVE_VERBATIM_SPAN_WORDS
from utils.enforcement.provenance import find_extractive_spans
from utils.fact_spans import extract_fact_spans, missing_fact_spans
from utils.voice_spec import longest_shared_run, voice_diagnostics

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kancity")


def _read(*parts):
    with open(os.path.join(FIXTURES, *parts), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def voice_documents():
    paths = sorted(glob.glob(os.path.join(FIXTURES, "voice", "*.txt")))
    assert len(paths) == 4, "the brand corpus fixture is incomplete"
    return [open(p, encoding="utf-8").read() for p in paths]


@pytest.fixture(scope="module")
def brain(voice_documents):
    """The measured half of the Brand Brain, built from the real corpus."""
    return BrandMetricsSQL.measure_documents(voice_documents)


@pytest.fixture(scope="module")
def treatment():
    return _read("treatment.txt")


@pytest.fixture(scope="module")
def gold():
    return _read("gold_script.txt")


@pytest.fixture(scope="module")
def rejected():
    return _read("rejected_draft.txt")


def blocking_failures(content, brain, source):
    """Every deterministic gate that would refuse this content, in one list."""
    failures = []
    failures += [f"mechanics: {f['message']}" for f in check_measured_mechanics(content, brain)]
    failures += [f"preflight: {f['message']}" for f in run_preflight_checks(content, brain)]
    failures += [
        f"copying: {span['length']} words \"{span['text'][:50]}\""
        for span in find_extractive_spans(content, source, max_span=NARRATIVE_VERBATIM_SPAN_WORDS)
    ]
    failures += [f"fact lost: \"{span}\"" for span in missing_fact_spans(content, extract_fact_spans(source))]
    return failures


class TestTheBrainBuiltFromTheRealCorpus:
    def test_the_spec_never_quotes_the_documents_it_measured(self, brain, voice_documents):
        for document in voice_documents:
            assert longest_shared_run(brain, document) < 8, (
                "the Brain reproduced its source corpus instead of describing it"
            )

    def test_the_corpus_reads_as_short_plain_sentences(self, brain):
        assert "Sentences run about 7 words" in brain
        assert "OPENING IS BUILT AS" in brain and "scene heading" in brain

    def test_register_is_measured_over_prose_not_the_appendices(self, brain):
        """Measured whole, this corpus came out at 6.3 four-syllable words per
        100 — a rate set by "protagonist" and "identity" in the closing
        analyses, which the draft then matched with "congregation"."""
        line = next(l for l in brain.splitlines()
                    if l.startswith("- four_plus_syllable_words_per_100_words:"))
        assert float(line.split(":")[1]) < 6.3


class TestTheCorrectVersionPasses:
    def test_it_clears_every_blocking_gate(self, gold, brain, treatment):
        assert blocking_failures(gold, brain, treatment) == [], (
            "the hand-written correct script is refused by the system that was asked to write it"
        )

    def test_keeping_the_facts_is_not_copying(self, gold, treatment):
        """It was. At the marketing threshold this script is flagged for
        reproducing the treatment's own facts, and the feedback that produced
        told the writer to reword them."""
        assert find_extractive_spans(gold, treatment, max_span=MAX_VERBATIM_SPAN_WORDS)
        assert find_extractive_spans(gold, treatment, max_span=NARRATIVE_VERBATIM_SPAN_WORDS) == []

    def test_its_plainness_is_described_but_never_blocked(self, gold, brain):
        """This script is plainer than the corpus — fewer abstractions, shorter
        sentences. That is a note for the voice pass to weigh, and it used to be
        a mechanical failure that sent the draft back round the loop."""
        assert check_measured_mechanics(gold, brain) == []
        notes = " ".join(voice_diagnostics(gold, brain))
        assert "Fewer abstract nouns" in notes and "usually fine" in notes


class TestTheRejectedDraftFails:
    def test_it_is_refused(self, rejected, brain, treatment):
        assert blocking_failures(rejected, brain, treatment), (
            "the draft that scored 6.8 and was force-approved still passes everything"
        )

    def test_the_fact_it_lost_is_named(self, rejected, treatment):
        assert missing_fact_spans(rejected, extract_fact_spans(treatment)) == ["more than seven men"], (
            "'They exceed seven' was not fabricated, not unattributed and not copied — "
            "the count was simply gone, and nothing objected"
        )

    def test_its_shape_is_described_as_the_list_it_is(self, rejected, brain):
        notes = " ".join(voice_diagnostics(rejected, brain))
        assert "Sentences are shorter" in notes and "read as a list, not a scene" in notes

    def test_it_is_further_from_the_brand_than_the_correct_version(self, rejected, gold, brain):
        bad_short = next(n for n in voice_diagnostics(rejected, brain) if "under 6 words" in n)
        good_short = next(n for n in voice_diagnostics(gold, brain) if "under 6 words" in n)
        assert float(bad_short.split("this draft ")[1].split(".")[0]) > \
               float(good_short.split("this draft ")[1].split(".")[0])
