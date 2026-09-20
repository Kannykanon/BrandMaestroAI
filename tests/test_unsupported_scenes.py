"""Whether the draft stayed inside its source.

Every other check in this system asks the opposite question — did the source
reach the draft — and a run got all the way through them while writing two acts
that do not exist. The treatment ends with Kan leaving, and the draft went on:

    INT. KAN'S HOSTEL ROOM — NIGHT
    Kan sits alone. He thinks about the day. He holds EMK's number. He
    remembers EMK's kindness. He remembers the men's faces.

The story gate was satisfied, because every beat it knew about had been told.
Nothing else was looking at what had been added.

The hard part is not noticing that those words are absent from the source. It
is that an adaptation invents on every line and must: measured word by word,
the hand-written script's closing scene is 0.34 grounded in its source and the
fabricated scene above is 0.33. A threshold drawn between them would have
refused the correct answer in order to catch half the fabrication.
"""
import pathlib

import pytest

from utils.coverage import RECYCLED, RETELLS_A_BEAT_WELL, unsupported_scenes

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "gold" / "kancity"


def _read(*parts):
    return (FIXTURES.joinpath(*parts)).read_text(encoding="utf-8")


SOURCE = _read("brief.txt")


class TestTheHandWrittenScriptIsNotRefused:
    def test_the_gold_invents_nothing(self):
        assert unsupported_scenes(_read("gold.txt"), SOURCE) == []

    def test_a_scene_that_only_retells_is_allowed_when_it_retells_clearly(self):
        """The gold has one scene carrying no beat an earlier scene had not
        already carried. It is not padding — it tells its beat at 0.73, where
        the fabricated scenes sit at 0.25 — and refusing it would be refusing
        a retelling for being a retelling."""
        assert RETELLS_A_BEAT_WELL < 0.73


class TestWritingOnPastTheSource:
    def test_both_invented_acts_are_found(self):
        found = unsupported_scenes(_read("rejected", "wrote_past_the_source.txt"), SOURCE)
        headings = " | ".join(f["heading"] for f in found)
        assert "NIGHT" in headings and "CONTINUOUS" in headings, headings

    def test_it_says_which_scene(self):
        found = unsupported_scenes(_read("rejected", "wrote_past_the_source.txt"), SOURCE)
        assert all(f["heading"] in f["message"][:200] for f in found)


class TestWhatItMustNotRefuse:
    def test_an_invented_transition_scene_is_left_alone(self):
        """A draft's own bridge between two source beats — Kan walking past the
        gates into an unknown part of the city — is screenwriting, not
        fabrication. The source does not contain it and never would. It was
        reported until recycling was required as well: it sits at 0.33 where
        the fabricated scenes sit at 0.58 and 0.64."""
        found = unsupported_scenes(_read("rejected", "dropped_the_qualifier.txt"), SOURCE)
        assert not any("LATE AFTERNOON" in f["heading"] for f in found), found

    def test_prose_with_no_scenes_is_not_judged(self):
        assert unsupported_scenes("A blog post. It has paragraphs.", SOURCE) == []

    def test_a_single_scene_cannot_repeat_itself(self):
        one = "INT. ROOM — DAY\nKan waits. Nobody comes."
        assert unsupported_scenes(one, SOURCE) == []

    @pytest.mark.parametrize("missing", ["", "   "])
    def test_nothing_to_compare_is_not_an_error(self, missing):
        assert unsupported_scenes(missing, SOURCE) == []
        assert unsupported_scenes(_read("gold.txt"), missing) == []


class TestTheThresholdsStayWhereTheEvidencePutThem:
    """Each was measured against the one hand-written script this system has,
    and each has a margin recorded in utils/coverage.py. A change to any of
    them without new writing to measure against is a guess."""

    def test_recycling_leaves_room_above_the_gold(self):
        assert RECYCLED > 0.35, "the gold's most repetitive scene sits at 0.35"

    def test_recycling_still_catches_a_recap(self):
        assert RECYCLED <= 0.58, "the fabricated scenes sit at 0.58 and 0.64"
