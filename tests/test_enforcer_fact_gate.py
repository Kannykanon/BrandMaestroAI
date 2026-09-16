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
- nominalisations_per_100_words: 1.2
- nominalisations_per_100_words_low: 0.8
- nominalisations_per_100_words_high: 3.1

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


# ---------------------------------------------------------------------------
#  Story the draft reached and then summarised away
# ---------------------------------------------------------------------------
TOLD = (
    "He calls after lectures.\n\n"
    "More than seven men. Seated. Drinking.\n\n"
    "They tell him to drink. He says no.\n\n"
    "Other men pass and greet the table. When they greet EMK, they use both hands.\n\n"
    "EMK asks about his studies. He sounds almost fatherly.\n\n"
    "Before Kan leaves, EMK gives him a number. Call me, he says, if you ever run into trouble.\n"
)

SUMMARISED = (
    "He calls after lectures.\n\n"
    "More than seven men. Seated. Drinking.\n\n"
    "They tell him to drink. He says no.\n\n"
    "EXT. CAMPUS - NIGHT\n\n"
    "This encounter marks his initiation into a world of hidden power, a place demanding "
    "loyalty and severe consequences. His choices now determine a path from which there is "
    "absolutely no return. His situation had changed beyond recognition.\n"
)

SOURCE_STORY = (
    "He finds a group of more than seven men, seated, drinking. They tell him to drink with "
    "them. He says no.\n\n"
    "Throughout the encounter, Kan notices a pattern: other rough-looking men drift by to greet "
    "the group, but when they greet EMK, they use both hands.\n\n"
    "As Kan grows restless, EMK's demeanor shifts. He starts asking Kan about his studies — "
    "almost fatherly. Before Kan goes, EMK gives him his personal number, tells him to call if "
    "he ever runs into trouble.\n"
)

NARRATIVE_BRAIN = BRAIN + "- nominalisations_per_100_words: 1.2\n- nominalisations_per_100_words_high: 3.1\n"


class TestStoryReplacedWithWhatItMeans:
    def test_a_script_that_summarises_its_ending_is_refused(self, enforcer):
        result = enforcer(SUMMARISED, research=SOURCE_STORY, content_type="script")
        assert result["approved"] is False and result["score"] == 0.0

    def test_no_model_call_is_spent_on_it(self, enforcer):
        enforcer(SUMMARISED, research=SOURCE_STORY, content_type="script")

    def test_the_feedback_names_what_went_missing(self, enforcer):
        feedback = enforcer(SUMMARISED, research=SOURCE_STORY, content_type="script")["feedback"]
        assert "STORY DROPPED" in feedback
        assert "hand" in feedback or "greet" in feedback
        assert "studi" in feedback or "fatherly" in feedback

    def test_the_feedback_says_what_to_write_instead(self, enforcer):
        feedback = enforcer(SUMMARISED, research=SOURCE_STORY, content_type="script")["feedback"]
        assert "A gesture, a question, a line of dialogue" in feedback
        assert "summary of a scene, not the scene" in feedback

    def test_the_version_that_tells_the_story_reaches_the_scoring_call(self, enforcer):
        with pytest.raises(_ModelWasCalled):
            enforcer(TOLD, research=SOURCE_STORY, content_type="script")

    def test_marketing_copy_is_not_asked_to_cover_all_of_its_research(self, enforcer):
        """A blog uses a fraction of its research on purpose. Demanding coverage
        there would be the tight-rule failure this change exists to undo.

        Written without reusing the source's wording, so this tests the coverage
        rule rather than the copying one.
        """
        blog = (
            "Recruitment rarely announces itself.\n\n"
            "It starts with something small: a favour, an invitation, a debt too minor to "
            "refuse.\n\n"
            "By the time the arrangement is visible, the obligation is already in place."
        )
        with pytest.raises(_ModelWasCalled):
            enforcer(blog, research=SOURCE_STORY, content_type="blog")
