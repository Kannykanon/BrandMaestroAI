"""Saying what something meant, instead of showing it.

The approved draft ended its most important beat like this:

    They use both hands. The gesture shows deference. It shows fear.

The hand-written script plays the same beat:

    When they greet EMK, they use both hands.
    Kan notices. He does not fully understand what it means. But he
    understands that it means something.

Same information. One states the meaning, the other lets a character fail to
take it. Nothing in the system could tell them apart — the words overlap
almost entirely, and the difference is who is speaking.
"""
import pytest

from tests.gold_pairs import discover
from utils.coverage import narrator_explanations


@pytest.fixture(scope="module")
def pair():
    return discover()[0]


class TestWhatIsCaught:
    def test_a_gesture_that_explains_itself(self):
        found = narrator_explanations("They use both hands. The gesture shows deference. It shows fear.")
        assert [f["sentence"] for f in found] == ["The gesture shows deference.", "It shows fear."]

    def test_the_padded_draft_is_caught_on_its_own(self, pair):
        """It wrote "It demonstrates profound deference." Nothing had flagged
        that sentence before; the gate it failed was about a lost fact."""
        draft = next(d for d in pair.rejected if d.name == "padded_register")
        assert narrator_explanations(draft.content)

    def test_the_message_quotes_the_sentence(self):
        found = narrator_explanations("The title carries real weight.")
        assert found and "carries real weight" in found[0]["message"]


class TestWhatIsLeftAlone:
    def test_the_hand_written_script(self, pair):
        assert narrator_explanations(pair.gold) == [], (
            "the answer this brand's reviewer wrote must not be told to show more"
        )

    def test_a_character_taking_the_meaning_is_the_scene(self):
        """The subject is the whole difference, and it is not always the first
        word: "But he understands that it means something" opens on a
        conjunction and is still somebody's own understanding."""
        assert narrator_explanations(
            "Kan notices. He does not fully understand what it means. "
            "But he understands that it means something."
        ) == []

    def test_a_person_doing_something_is_not_explaining(self):
        assert narrator_explanations("He shows deference. She signals the others. EMK marks the page.") == []

    def test_a_thing_doing_something_physical_is_not_explaining(self):
        """"Shows" is only telling when what follows is a meaning."""
        assert narrator_explanations("The door shows a long scratch. The ledger shows three names.") == []

    def test_prose_with_no_explaining_in_it(self):
        assert narrator_explanations("He sits. He waits. The road is closing.") == []

    def test_nothing_to_read(self):
        assert narrator_explanations("") == []


class TestItIsAGateInTheHarness:
    def test_the_gold_clears_it(self, pair):
        from tests.gold_pairs import failures

        assert "explaining" not in failures(pair.gold, pair)

    def test_narrative_only_in_the_enforcer(self):
        """A proposal explains what things mean for a living; a scene does not."""
        import inspect

        from nodes import enforcer

        source = inspect.getsource(enforcer.enforcer_node)
        assert "narrator_explanations(content)" in source
        assert source.index("is_narrative") < source.index("narrator_explanations(content)")
