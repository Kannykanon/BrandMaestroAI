"""The scene planner annotates shots; it cannot change their words or break the plan."""
import json
from types import SimpleNamespace

from youtube.planner import annotate_shots
from youtube.script_parser import plan_shots

SCRIPT = """INT. SHOP - NIGHT
The doors opened.

MAYA: We did it.
LEO: Did we?
"""


class _LLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(content=self.reply if isinstance(self.reply, str) else json.dumps(self.reply))


def shots():
    return plan_shots(SCRIPT)[1]


def test_valid_annotations_are_applied():
    reply = {
        "shots": [
            {"position": 1, "shot_type": "narration", "visual": "A dark shop, doors swinging open.", "characters": []},
            {"position": 2, "shot_type": "two_character", "visual": "Maya grins at Leo.", "characters": ["MAYA", "LEO"]},
            {"position": 3, "shot_type": "dialogue", "visual": "Leo, unsure.", "characters": ["LEO"]},
        ],
        "speakers": {"MAYA": "determined founder", "LEO": "sceptical partner"},
    }
    result = annotate_shots(shots(), "long_form", llm=_LLM(reply))
    assert result.used_fallback is False
    assert result.shots[2].shot_type == "two_character"
    assert result.shots[2].characters == ["MAYA", "LEO"]
    assert result.shots[1].visual == "A dark shop, doors swinging open."
    assert result.speakers == {"MAYA": "determined founder", "LEO": "sceptical partner"}


def test_the_model_never_sees_a_request_to_return_script_text():
    llm = _LLM({"shots": []})
    planned = shots()
    before = [s.text for s in planned]
    annotate_shots(planned, "short", llm=llm)
    assert [s.text for s in planned] == before
    assert "Do not rewrite" in llm.prompts[0]
    assert "Short (vertical" in llm.prompts[0]


def test_narrator_shots_are_always_narration():
    reply = {"shots": [{"position": 1, "shot_type": "dialogue", "visual": "x", "characters": ["MAYA"]}]}
    result = annotate_shots(shots(), "long_form", llm=_LLM(reply))
    assert result.shots[1].shot_type == "narration"


def test_invalid_values_fall_back_per_field():
    reply = {"shots": [
        {"position": 2, "shot_type": "explosion", "visual": "", "characters": ["STRANGER", 7]},
        {"position": 99, "shot_type": "dialogue", "visual": "ignored"},
        {"position": 2, "shot_type": "cutaway", "visual": "duplicate ignored"},
        "not an object",
    ]}
    result = annotate_shots(shots(), "long_form", llm=_LLM(reply))
    shot = result.shots[2]
    assert shot.shot_type == "dialogue"          # unknown type rejected
    assert shot.visual == "MAYA speaking"        # empty visual rejected
    assert shot.characters == ["MAYA"]           # unknown names dropped, speaker kept on screen
    assert set(result.shots) == {1, 2, 3}
    assert result.used_fallback is True          # shots 1 and 3 got defaults


def test_model_failure_or_bad_json_falls_back_to_defaults():
    for reply in (RuntimeError("timeout"), "not json at all"):
        result = annotate_shots(shots(), "long_form", llm=_LLM(reply))
        assert result.used_fallback is True
        assert [result.shots[p].shot_type for p in (1, 2, 3)] == ["narration", "dialogue", "dialogue"]
        assert result.shots[1].visual == "INT. SHOP - NIGHT"  # the scene direction is the best default


def test_speaker_notes_only_for_known_speakers():
    reply = {"shots": [], "speakers": {"maya": "founder", "GHOST": "not in the script", "LEO": 5}}
    result = annotate_shots(shots(), "long_form", llm=_LLM(reply))
    assert result.speakers == {"MAYA": "founder"}
