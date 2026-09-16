"""Story the draft reached and then summarised away.

The act that prompted this, generated from a treatment that had EMK asking
about Kan's studies, other men greeting him with both hands, and a phone number
handed over with "call me if you ever run into trouble":

    Kan's situation changed, becoming dangerous with no clear escape.
    His life changed irrevocably.
    This encounter marks his initiation into a world of hidden power, a place
    demanding loyalty and severe consequences.
    His choices now determine a path from which there is absolutely no return.

Nothing there is false, copied, unattributed or measurably off-brand. Every
gate in the system passed it. It had simply stopped telling the story and
started describing what the story means, and lost four beats doing it.
"""
import os

import pytest

from utils.coverage import dropped_detail, story_beats, vague_sections

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kancity")

# The top of this brand's own abstraction range, measured from the four
# screenplays in fixtures/kancity/voice (see test_brand_voice_regression).
BRAND_HIGH = 3.1


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def treatment():
    return _read("treatment.txt")


@pytest.fixture(scope="module")
def gold():
    return _read("gold_script.txt")


class TestWhatCountsAsStory:
    def test_the_cast_list_is_not_a_beat_to_cover(self, treatment):
        """A retelling does not have to reproduce the character notes, and the
        sub-heading for each name must not turn the section back on."""
        joined = " ".join(story_beats(treatment)).lower()
        assert "loose ensemble" not in joined and "outward" not in joined
        assert "function more as an atmosphere" not in joined

    def test_the_story_itself_is(self, treatment):
        joined = " ".join(story_beats(treatment)).lower()
        assert "both hands" in joined and "asking kan about his studies" in joined


class TestDroppedDetail:
    def test_the_hand_written_retelling_keeps_every_beat_it_covers(self, gold, treatment):
        assert dropped_detail(gold, treatment, BRAND_HIGH) == [], (
            "the correct version must not be told it dropped the story"
        )

    def test_a_beat_told_without_its_detail_is_reported(self, gold, treatment):
        summarised = gold.replace(
            "EMK asks about his studies.", "").replace(
            "He sounds almost fatherly.", "").replace(
            "Before Kan leaves, EMK gives him a number.", "").replace(
            "Call me, he says, if you ever run into trouble.",
            "This encounter marks his initiation into a world of hidden power.")
        findings = dropped_detail(summarised, treatment, BRAND_HIGH)
        assert findings, "replacing a scene with what it means went unnoticed"
        missing = " ".join(w for f in findings for w in f["missing"])
        assert "studi" in missing or "fatherly" in missing

    def test_a_beat_told_thinly_is_not_called_untold(self, gold, treatment):
        """The fix differs, so the feedback has to differ: a thinned beat needs
        its detail put back, a beat never told needs writing at all."""
        without_greeting = gold.replace("When they greet EMK, they use both hands.", "").replace(
            "Other men pass by. Rough men. Men with reputations.", "").replace(
            "They greet the table.", "").replace(
            "Kan notices. He does not fully understand what it means. "
            "But he understands that it means something.", "")
        thinned = dropped_detail(without_greeting, treatment, BRAND_HIGH)
        assert thinned and not thinned[0]["skipped"], "what is left still gestures at the beat"

    def test_the_generated_draft_that_told_none_of_its_ending(self, treatment):
        """The real second run. It tells the first five beats, then replaces the
        last four with two sentences about what they all mean."""
        findings = dropped_detail(_read("second_run_draft.txt"), treatment, BRAND_HIGH)
        assert len(findings) == 4, "the whole collapsed ending must be reported, not one beat of it"
        assert any(f["skipped"] for f in findings), (
            "the two-handed greetings are not in this draft at all"
        )
        missing = " ".join(w for f in findings for w in f["missing"])
        for detail in ("hand", "greet", "studi", "fatherly"):
            assert detail in missing, f"the feedback never mentions {detail}"

    def test_a_piece_that_covers_the_opening_and_stops_has_chosen_its_scope(self, treatment):
        """Nothing past the last beat a draft engages with is demanded, or every
        trailer would be refused for not being the whole treatment."""
        trailer = (
            "A new academic year.\n\n"
            "KAN arrives on campus for the first time, buzzing.\n\n"
            "He is the last among his friend group to gain admission.\n\n"
            "Stepping onto the grounds feels like catching up. Like he belongs.\n"
        )
        assert dropped_detail(trailer, treatment, BRAND_HIGH) == []

    def test_no_source_means_nothing_to_drop(self, gold):
        assert dropped_detail(gold, "", BRAND_HIGH) == [] and dropped_detail("", "some source text here", BRAND_HIGH) == []


class TestVagueSections:
    BRAND_HIGH = 3.1

    def test_the_act_that_gave_up_is_found(self, gold):
        collapsed = gold.split("STRUCTURAL LESSON")[0] + (
            "\n\nEXT. CAMPUS — NIGHT\n\n"
            "Kan's situation changed, becoming dangerous with no clear escape. "
            "His life changed irrevocably. This encounter marks his initiation into a world of "
            "hidden power, a place demanding loyalty and severe consequences. His choices now "
            "determine a path from which there is absolutely no return."
        )
        findings = vague_sections(collapsed, self.BRAND_HIGH)
        assert findings, "a section written entirely in abstractions passed unremarked"
        assert "initiation into a world of hidden power" in " ".join(findings[0]["examples"])

    def test_the_hand_written_version_is_left_alone(self, gold):
        assert vague_sections(gold, self.BRAND_HIGH) == []

    def test_a_section_is_judged_against_the_rest_of_its_own_draft(self, gold):
        """Against the brand's range alone this act sits just under any fixed
        multiple. What gives it away is running at three times the acts before
        it — a piece that starts concrete and gives up."""
        findings = vague_sections(gold.split("STRUCTURAL LESSON")[0] + (
            "\n\nEXT. CAMPUS — NIGHT\n\n"
            "Kan's situation changed, becoming dangerous with no clear escape. "
            "His life changed irrevocably. This encounter marks his initiation into a world of "
            "hidden power, a place demanding loyalty and severe consequences. His choices now "
            "determine a path from which there is absolutely no return."
        ), self.BRAND_HIGH)
        assert findings[0]["rate"] < self.BRAND_HIGH * 1.5, (
            "this is the case a fixed multiple of the brand's range misses"
        )

    def test_an_unmeasured_brand_says_nothing(self, gold):
        assert vague_sections(gold, 0.0) == []
