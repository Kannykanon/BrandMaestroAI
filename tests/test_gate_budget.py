"""Two bugs from one run: boilerplate read as story, and a gate that starved the rest.

The research blob the writer works from is assembled by the researcher, which
introduces each source with a banner and a note explaining how to treat it. The
note under the first banner is ordinary prose, so the story-coverage check read
it as a beat, and a screenplay was told this for six consecutive rounds:

    NOT TOLD AT ALL: "Facts about the subject itself come from here. Where this
    and the external context below disagree, this wins."

Nothing could satisfy it. The run spent its whole revision budget there, ended
"Needs review" with the rounds exhausted, and — because the story gate returns
before the scoring call — the voice pass was never invoked once in the entire
generation. The draft that came out read "He needs cash. His wallet holds
money." and "Shadows fall. Shadows lengthen.", and nothing ever looked at it.
"""
import pytest

import graph.deps
from nodes.enforcer import enforcer_node
from utils.coverage import dropped_detail, story_beats
from utils.research_sections import (
    NO_RESEARCH,
    external_section,
    source_section,
    strip_framing,
)

STORY = (
    "Kan loses his wallet after lectures.\n\n"
    "He finds a group of more than seven men, seated, drinking. They tell him to drink.\n\n"
    "Throughout the encounter, other men drift by to greet the group, and when they greet EMK "
    "they use both hands.\n\n"
    "EMK asks about his studies, almost fatherly, and gives him his personal number before he goes.\n"
)


class TestThePipelineDoesNotAskForItsOwnNotesBack:
    def test_the_note_under_a_banner_is_not_a_story_beat(self):
        beats = " ".join(story_beats(source_section(STORY)))
        assert "Facts about the subject itself come from here" not in beats, (
            "a draft was told for six rounds to dramatise a note about source precedence"
        )
        assert "disagree" not in beats

    def test_the_web_search_note_is_not_one_either(self):
        blob = source_section(STORY) + "\n\n" + external_section("Reception has been strong.")
        beats = " ".join(story_beats(blob))
        assert "Do NOT use it to assert facts" not in beats
        assert "framing and timeliness" not in beats

    def test_the_sources_themselves_survive_intact(self):
        beats = " ".join(story_beats(source_section(STORY)))
        assert "both hands" in beats and "asks about his studies" in beats
        assert "more than seven men" in beats

    def test_a_draft_that_tells_the_story_is_no_longer_refused(self):
        told = (
            "Kan loses his wallet after lectures.\n\n"
            "He finds more than seven men. Seated. Drinking. They tell him to drink.\n\n"
            "Other men drift by and greet the group. When they greet EMK, they use both hands.\n\n"
            "EMK asks about his studies. He sounds almost fatherly. He gives him his personal "
            "number before he goes.\n"
        )
        assert dropped_detail(told, source_section(STORY), 3.1) == [], (
            "the leaked note was reported as a dropped beat however well the draft was written"
        )

    def test_stripping_is_matched_against_what_was_written_not_guessed(self):
        """A pattern for "instructional prose" would eventually eat a real
        source paragraph, and losing the brand's material is the worse of the
        two failures."""
        kept = strip_framing(source_section("Use this only as background. The goal is clarity."))
        assert "Use this only as background. The goal is clarity." in kept

    def test_an_empty_research_blob_is_left_alone(self):
        assert strip_framing("") == ""
        assert strip_framing(NO_RESEARCH) == ""


# ---------------------------------------------------------------------------
#  A gate that has had its rounds hands the draft on
# ---------------------------------------------------------------------------
BRAIN = """# BRAND VOICE PROFILE
Short, plain sentences.

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

SOURCE = (
    "After lectures he calls the number. He finds a group of more than seven men, seated, "
    "drinking. They tell him to drink with them. He says no."
)

LOST_THE_COUNT = (
    "He calls after lectures.\n\n"
    "He observes a congregation. Numerous individuals are present. They exceed seven.\n\n"
    "They tell him to drink. He says no."
)

FACT_GATE = "state the source's facts exactly"


class _Analyzer:
    def get_context(self):
        return BRAIN


class _ModelWasCalled(Exception):
    """Raised in place of the scoring call, to show the draft reached it."""


@pytest.fixture
def enforcer(monkeypatch):
    def run(content, history=(), **state):
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
            "research": SOURCE,
            "content": content,
            "iteration": 1,
            "violation_history": list(history),
        }
        return enforcer_node({**base, **state})

    return run


class TestAGateGetsItsRoundsThenSharesTheDraft:
    def test_the_first_time_it_still_short_circuits(self, enforcer):
        """The finding is certain and the fix is specific, so a scoring call
        would be money spent to say the same thing."""
        result = enforcer(LOST_THE_COUNT)
        assert result["approved"] is False and result["score"] == 0.0
        assert "FACTS LOST" in result["feedback"]

    def test_after_its_rounds_the_draft_reaches_the_scoring_call(self, enforcer):
        """Otherwise an unsatisfiable finding leads every round, and the voice
        pass — the only thing that would notice "He needs cash. His wallet holds
        money." — is never invoked at all."""
        history = [f"{FACT_GATE}: round one", f"{FACT_GATE}: round two"]
        with pytest.raises(_ModelWasCalled):
            enforcer(LOST_THE_COUNT, history=history)

    def test_an_unrelated_history_does_not_spend_the_budget(self, enforcer):
        result = enforcer(LOST_THE_COUNT, history=["extractive copying", "banned punctuation"])
        assert result["score"] == 0.0, "another gate's rounds are not this gate's rounds"

    def test_a_draft_with_nothing_wrong_reaches_the_call_as_before(self, enforcer):
        kept = "He calls after lectures.\n\nMore than seven men. Seated. Drinking.\n\nHe says no."
        with pytest.raises(_ModelWasCalled):
            enforcer(kept)


class _Scored:
    """A scoring model that approves everything, to isolate the gate's own verdict."""

    def __init__(self, payload):
        self.payload = payload

    def invoke(self, _prompt):
        import json
        from types import SimpleNamespace
        return SimpleNamespace(content=json.dumps(self.payload))


CLEAN_EVALUATION = {
    "hallucination_check": {"verdict": "PASS", "hallucinated_claims": []},
    "structural_precheck": {"opening_pattern": True, "closing_pattern": True,
                            "signature_constructions": {}, "evidence_anchoring": True,
                            "sentence_rhythm": True, "hedging_violations": []},
    "voice_score": 9.0,
    "voice_rewrites": [],
    "approved": True,
    "directive_compliance": "NOT_APPLICABLE",
    "publishability": "PASS",
    "score": 9.0,
    "style_match": 0.9, "tone_match": 0.9, "structure_match": 0.9, "signature_match": 0.9,
    "feedback": "",
    "flagged_passages": [],
    "creative_angle": "a retelling",
}


@pytest.fixture
def scored_enforcer(monkeypatch):
    def run(content, history=(), **state):
        monkeypatch.setattr(
            graph.deps, "resolve_deps",
            lambda business_id, content_type: (None, _Analyzer(), None),
        )
        monkeypatch.setattr("model.LLMSingleton.get", lambda *a, **k: _Scored(CLEAN_EVALUATION))
        base = {
            "business_id": "b1",
            "content_type": "script",
            "topic": "a student loses his wallet",
            "research": SOURCE,
            "content": content,
            "iteration": 1,
            "violation_history": list(history),
        }
        return enforcer_node({**base, **state})

    return run


class TestCarryingChangesWhenNotWhether:
    HAD_ITS_ROUNDS = [f"{FACT_GATE}: round one", f"{FACT_GATE}: round two"]

    def test_a_carried_finding_still_refuses_the_draft(self, scored_enforcer):
        result = scored_enforcer(LOST_THE_COUNT, history=self.HAD_ITS_ROUNDS)
        assert result["approved"] is False, "the scoring model approved it; the gate must not"
        assert result["score"] <= 5.0

    def test_the_writer_gets_the_finding_and_the_evaluation_together(self, scored_enforcer):
        feedback = scored_enforcer(LOST_THE_COUNT, history=self.HAD_ITS_ROUNDS)["feedback"]
        assert "FACTS LOST" in feedback and "more than seven men" in feedback

    def test_it_is_never_force_approved_when_the_rounds_run_out(self, scored_enforcer):
        """Before carrying, this gate returned early and the run simply ended
        unapproved. Reaching the end of the loop must not turn a lost fact into
        a quality shortfall that ships."""
        from utils.enforcement import MAX_ITERATIONS

        result = scored_enforcer(LOST_THE_COUNT, history=self.HAD_ITS_ROUNDS,
                                 iteration=MAX_ITERATIONS)
        assert result["approved"] is False

    def test_a_draft_with_nothing_carried_is_approved_normally(self, scored_enforcer):
        kept = "He calls after lectures.\n\nMore than seven men. Seated. Drinking.\n\nHe says no."
        assert scored_enforcer(kept)["approved"] is True

    def test_the_gate_is_recorded_so_it_cannot_lead_every_round(self, scored_enforcer):
        history = scored_enforcer(LOST_THE_COUNT, history=self.HAD_ITS_ROUNDS)["violation_history"]
        assert sum(1 for v in history if v.startswith(FACT_GATE)) >= 2


# ---------------------------------------------------------------------------
#  A document's title block is not a scene
# ---------------------------------------------------------------------------
TITLE_BLOCK = (
    "Film / Limited Series Treatment - Based on True Events\n"
    "Status: Part 1 of an ongoing account. Additional installments to be added "
    "as the story continues.\n"
)


class TestTheDocumentTalkingAboutItself:
    def test_a_title_block_is_not_a_beat_to_dramatise(self):
        """Reported as story in every round of a run: "missing from the draft:
        account, add, additional, bas, continu, event, film, limit". There is no
        scene there and no draft could have satisfied it."""
        from utils.coverage import _is_about_the_document

        assert _is_about_the_document(TITLE_BLOCK)
        assert story_beats(TITLE_BLOCK + "\n\n" + STORY) == story_beats(STORY)

    def test_it_is_caught_without_its_heading(self):
        """Retrieval returns chunks, so the heading that would have marked this
        as front matter is often gone by the time anything reads it."""
        from utils.coverage import _is_about_the_document

        soup = " ".join(TITLE_BLOCK.split())
        assert _is_about_the_document(soup)

    def test_the_story_is_not_mistaken_for_metadata(self):
        from utils.coverage import _is_about_the_document

        for beat in ("He finds a group of more than seven men, seated, drinking.",
                     "EMK asks about his studies. He gives him a number.",
                     "They greet EMK with both hands. Kan notices."):
            assert not _is_about_the_document(beat), beat

    def test_coverage_prefers_the_document_over_what_retrieval_returned(self):
        """The check compares against reference_documents() when the brand has
        them, and only falls back to the research blob when it does not."""
        import inspect

        from nodes import enforcer

        source = inspect.getsource(enforcer.enforcer_node)
        assert "reference_documents(state[\"business_id\"]" in source
        assert "or research_text" in source, "a brand with no product document must still be checked"


class TestFabricationFeedbackFitsTheContent:
    PAYLOAD = dict(
        CLEAN_EVALUATION,
        hallucination_check={"verdict": "FAIL",
                             "hallucinated_claims": ["The men stand there. They watch the street."]},
        approved=True,
    )

    def _feedback(self, monkeypatch, content_type):
        monkeypatch.setattr(
            graph.deps, "resolve_deps",
            lambda business_id, ct: (None, _Analyzer(), None),
        )
        monkeypatch.setattr("model.LLMSingleton.get", lambda *a, **k: _Scored(self.PAYLOAD))
        kept = "He calls after lectures.\n\nMore than seven men. Seated. Drinking.\n\nHe says no."
        return enforcer_node({
            "business_id": "b1", "content_type": content_type, "topic": "t",
            "research": SOURCE, "content": kept, "iteration": 1, "violation_history": [],
        })["feedback"]

    def test_a_script_is_sent_back_to_its_source_not_to_an_asset_bank(self, monkeypatch):
        """A screenplay has no client counts. Told to "use only exact client
        counts, percentages, and framework names", its writer goes looking for a
        list it was never given."""
        feedback = self._feedback(monkeypatch, "script")
        assert "Go back to the source" in feedback
        assert "client counts" not in feedback and "asset bank" not in feedback

    def test_marketing_copy_still_gets_the_asset_bank(self, monkeypatch):
        feedback = self._feedback(monkeypatch, "blog")
        assert "brand asset bank" in feedback and "client counts" in feedback

    def test_a_script_is_told_its_own_names_are_not_the_problem(self, monkeypatch):
        assert "are not claims" in self._feedback(monkeypatch, "script")
