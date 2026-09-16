"""The Enforcer refuses a draft that lost one of its source's facts.

The draft that prompted this passed every check in the file. The treatment said
"He finds a group of more than seven men"; the draft said "He observes a
congregation. Numerous individuals are present. They exceed seven." Nothing
objected, because nothing was looking: the claim was not fabricated, not
attributed to anyone, and — after the copying gate had told the writer to
discard the source's wording — no longer copied either.

It is checked before the scoring call for the same reason the other
deterministic gates are: a model asked to notice a missing qualifier will
sometimes notice it.
"""
import pytest

import graph.deps
from nodes.enforcer import enforcer_node

BRAIN = """# BRAND VOICE PROFILE
This brand writes in short, plain sentences.

# PUNCTUATION HABITS
- Em dashes: never

# MEASURED MECHANICS
- exclamation_marks_per_100_words: 0.0
- median_words_per_sentence: 7.0
- bracket_placeholders_per_100_words: 0.0

# STRUCTURAL PATTERNS
Opens on a scene.
"""

TREATMENT = (
    "After lectures, he calls the number. A voice tells him to come outside the school "
    "premises. He finds a group of more than seven men, seated, drinking. They tell him "
    "to drink with them. He says no."
)

LOST_THE_COUNT = (
    "He calls after lectures.\n\n"
    "He observes a congregation. Numerous individuals are present. They exceed seven.\n\n"
    "They tell him to drink. He says no."
)

KEPT_THE_COUNT = (
    "He calls after lectures.\n\n"
    "More than seven men. Seated. Drinking.\n\n"
    "They tell him to drink. He says no."
)


class _Analyzer:
    def get_context(self):
        return BRAIN


class _ModelWasCalled(Exception):
    """Raised in place of an LLM call, to prove the gate did not stop here."""


@pytest.fixture
def enforcer(monkeypatch):
    def run(content, research=TREATMENT, **state):
        monkeypatch.setattr(
            graph.deps, "resolve_deps",
            lambda business_id, content_type: (None, _Analyzer(), None),
        )
        monkeypatch.setattr(
            "model.LLMSingleton.get",
            lambda *a, **k: (_ for _ in ()).throw(_ModelWasCalled()),
        )
        base = {
            "business_id": "b1",
            "content_type": "script",
            "topic": "a student loses his wallet",
            "research": research,
            "content": content,
            "iteration": 1,
        }
        return enforcer_node({**base, **state})

    return run


class TestADroppedFactIsRefused:
    def test_it_is_not_approved(self, enforcer):
        assert enforcer(LOST_THE_COUNT)["approved"] is False

    def test_no_model_call_is_spent_on_it(self, enforcer):
        enforcer(LOST_THE_COUNT)  # _ModelWasCalled would escape if it got that far

    def test_the_feedback_names_the_fact(self, enforcer):
        feedback = enforcer(LOST_THE_COUNT)["feedback"]
        assert "more than seven men" in feedback
        assert "FACTS LOST" in feedback

    def test_the_feedback_does_not_ask_for_the_sources_sentence_back(self, enforcer):
        """The fix is the brand's sentence carrying the fact, not the
        treatment's sentence reinstated — that would fail the copying gate, and
        alternating between the two is how an earlier draft burned six rounds."""
        feedback = enforcer(LOST_THE_COUNT)["feedback"]
        assert "do not rebuild the source's sentence" in feedback

    def test_the_flagged_passage_is_recorded(self, enforcer):
        assert "fact dropped" in enforcer(LOST_THE_COUNT)["flagged_passages"]


class TestKeepingTheFactIsNotRefused:
    def test_the_hand_written_version_reaches_the_scoring_call(self, enforcer):
        """It gets past every deterministic gate, including the copying one:
        keeping "more than seven men" is not copying a sentence."""
        with pytest.raises(_ModelWasCalled):
            enforcer(KEPT_THE_COUNT)

    def test_a_piece_that_never_covers_the_scene_is_not_refused_for_it(self, enforcer):
        """Scope is an editorial choice. Stating a fact wrongly is not."""
        with pytest.raises(_ModelWasCalled):
            enforcer("A city. A student. A debt he cannot pay.")
