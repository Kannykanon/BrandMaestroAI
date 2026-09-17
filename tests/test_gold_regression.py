"""Score every rubric change against writing already judged good.

The one property that matters here: a correction made after reading a bad run
must not make the system refuse the right answer. It nearly has, more than
once. A judge briefed to fix a draft's sentence lengths would have pushed the
hand-written Kancity script away from correct, because that script is plainer
and more clipped than the corpus it was written for — on every axis, on purpose.

These run without a model or a database, so they cost nothing and run on every
commit. Add a pair by adding a directory; see tests/gold_pairs.py.
"""
import pytest

from tests.gold_pairs import GATES, discover, failures

PAIRS = discover()
REJECTED = [(pair, draft) for pair in PAIRS for draft in pair.rejected]


def test_there_are_pairs_to_check():
    assert PAIRS, "no gold pairs found — the harness is checking nothing"


@pytest.mark.parametrize("pair", PAIRS, ids=str)
class TestTheRightAnswerPasses:
    def test_the_gold_clears_every_gate(self, pair):
        found = failures(pair.gold, pair)
        assert found == {}, (
            f"the hand-written correct answer for {pair.name} is refused by "
            f"{', '.join(found)}: {found}"
        )

    def test_the_brief_itself_is_not_the_answer(self, pair):
        """The gold is a retelling, not a copy. If pasting the brief passed
        every gate, the gates would be measuring nothing."""
        assert failures(pair.brief, pair) != {}

    def test_the_brain_never_quotes_the_corpus_it_measured(self, pair):
        from utils.voice_spec import longest_shared_run

        for document in pair.voice:
            assert longest_shared_run(pair.brain, document) < 8

    def test_the_habits_given_to_the_writer_carry_no_rates(self, pair):
        """Every number in this spec has been chased by a writer or cited by a
        judge to fault a draft that crossed it."""
        import re

        brain = pair.brain
        if "SENTENCE AND PARAGRAPH HABITS:" not in brain:
            pytest.skip("no voice spec measured for this corpus")
        habits = brain.split("SENTENCE AND PARAGRAPH HABITS:")[1].split("HOW ITS")[0]
        assert not re.search(r"\d", habits), habits


@pytest.mark.parametrize("pair,draft", REJECTED, ids=lambda v: getattr(v, "name", v))
class TestTheKnownBadDraftsAreRefused:
    def test_it_is_refused(self, pair, draft):
        assert failures(draft.content, pair), (
            f"{draft.name} shipped once and must never pass again"
        )

    def test_the_gate_that_catches_it_is_the_one_named(self, pair, draft):
        """Which gate catches a draft is the point, not merely that one does.
        A fact quietly dropped and a scene summarised away need different
        feedback, and a refusal for the wrong reason sends the writer somewhere
        useless — six rounds of that is a run wasted."""
        if not draft.expect:
            pytest.skip("no expected gate recorded for this draft")
        assert draft.expect in GATES, f"unknown gate {draft.expect!r}"
        found = failures(draft.content, pair)
        assert draft.expect in found, (
            f"{draft.name} should be caught by {draft.expect}, was caught by {list(found)}"
        )
