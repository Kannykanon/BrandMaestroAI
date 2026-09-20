"""The voice pass may not suggest a line the rest of the enforcer will refuse.

Asked for a sentence in the brand's voice, it offered:

    This marks the beginning of his entanglement.

That is the source treatment's own commentary. It is also, word for word, the
example the enforcer's own prompt gives of a genuine failure — "Copying the
brief's own commentary instead of dramatising it". The writer took the
suggestion, the told-not-shown check flagged it two rounds later, and the run
reached its cap without ever being approved.

A suggestion the system will not accept is worse than no suggestion: it costs
the round that adopts it and the round that removes it.
"""
import inspect

import pytest

import nodes.enforcer as enforcer
from utils.coverage import narrator_explanations


class TestTheCheckAgrees:
    """The filter defers to told-not-shown rather than inventing a second rule."""

    def test_the_suggestion_that_caused_this_is_caught_by_it(self):
        assert narrator_explanations("This marks the beginning of his entanglement.")

    def test_the_good_suggestions_from_the_same_run_are_not(self):
        for line in ("Kan, a freshman, arrives full of hope and ambition.",
                     "The gesture is a signal: status, deference, fear.",
                     "He meets his friends. He does not check his pockets.",
                     "Stepping onto the grounds feels like catching up."):
            assert narrator_explanations(line) == [], line


class TestTheFilterIsWiredIn:
    def test_rewrites_are_filtered_before_they_reach_the_writer(self):
        source = inspect.getsource(enforcer.enforcer_node)
        block = source.split("voice_rewrites", 1)[1].split("voice_subject", 1)[0]
        assert "narrator_explanations" in block

    def test_it_is_narrative_only(self):
        """A proposal explains what things mean for a living."""
        source = inspect.getsource(enforcer.enforcer_node)
        block = source.split("voice_rewrites", 1)[1].split("voice_subject", 1)[0]
        assert "is_narrative" in block

    def test_the_cap_is_applied_after_filtering_not_before(self):
        """Slicing first and filtering after would let one bad suggestion cost
        a usable one, which is the opposite of the point."""
        source = inspect.getsource(enforcer.enforcer_node)
        block = source.split("voice_rewrites", 1)[1].split("voice_subject", 1)[0]
        assert block.index("narrator_explanations") < block.index("[:5]")


@pytest.mark.parametrize("bad", [
    "This marks the beginning of his entanglement.",
    "The gesture shows deference.",
    "This title carries real weight.",
])
def test_the_shapes_that_get_dropped(bad):
    assert narrator_explanations(bad), bad
