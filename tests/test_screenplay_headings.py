"""Scene headings: the brand's format, and whether the heading is true.

A draft came back with "INT. UNIVERSITY CAMPUS, DAY" — a comma where this
brand's scripts use an em dash, and INT. for a place that is outdoors. Nothing
objected to either, because nothing was reading headings at all.
"""
import glob
import os

import pytest

from utils.screenplay import (
    brand_separator,
    heading_findings,
    headings,
    sanitize_scene_headings,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "gold", "kancity")


@pytest.fixture(scope="module")
def corpus():
    return "\n".join(open(p, encoding="utf-8").read()
                     for p in glob.glob(os.path.join(FIXTURES, "voice", "*.txt")))


@pytest.fixture(scope="module")
def gold():
    with open(os.path.join(FIXTURES, "gold.txt"), encoding="utf-8") as handle:
        return handle.read()


DRAFT = (
    "ACT I\n"
    "INT. UNIVERSITY CAMPUS, DAY\n"
    "KAN arrives on campus for the first time.\n\n"
    "EXT. OUTSIDE SCHOOL PREMISES, LATE AFTERNOON\n"
    "He finds a group of more than seven men.\n"
)


class TestReadingHeadings:
    def test_it_finds_them_and_leaves_prose_alone(self):
        found = headings(DRAFT)
        assert [h[2].upper() for h in found] == ["INT.", "EXT."]
        assert all("KAN arrives" not in h[1] for h in found)

    def test_the_separator_is_measured_from_the_brands_own_scripts(self, corpus):
        assert brand_separator(corpus) == "—"

    def test_a_brand_with_no_headings_gets_the_ordinary_default(self):
        assert brand_separator("We write blog posts. No scene headings here.") == "—"


class TestHeadingsAreCorrectedInCode:
    def test_a_comma_becomes_the_brands_em_dash(self, corpus):
        fixed, fixes = sanitize_scene_headings(DRAFT, corpus)
        assert "EXT. OUTSIDE SCHOOL PREMISES — LATE AFTERNOON" in fixed
        assert len(fixes) == 2

    def test_an_outdoor_place_marked_indoors_is_put_right(self, corpus):
        """No judgement is needed to know which side of a door a campus is on,
        so it is corrected rather than sent back for a revision round."""
        fixed, _ = sanitize_scene_headings(DRAFT, corpus)
        assert "EXT. UNIVERSITY CAMPUS — DAY" in fixed
        assert "INT. UNIVERSITY CAMPUS" not in fixed

    def test_nothing_is_left_for_the_advisory_to_find(self, corpus):
        fixed, _ = sanitize_scene_headings(DRAFT, corpus)
        assert heading_findings(fixed) == []

    def test_an_ambiguous_place_is_never_guessed_at(self, corpus):
        """This corpus writes both INT. DINER and EXT. DINER, for the two sides
        of one door. A sanitiser that picked one would be inventing."""
        both = "INT. DINER — DAY\nEXT. DINER — LATER\nINT./EXT. CAR — NIGHT\n"
        assert sanitize_scene_headings(both, corpus) == (both, [])

    def test_the_hand_written_script_is_not_touched(self, gold, corpus):
        """It already uses the brand's separator. A sanitiser that rewrites the
        right answer is worse than no sanitiser."""
        fixed, fixes = sanitize_scene_headings(gold, corpus)
        assert fixes == [] and fixed == gold

    def test_prose_is_never_rewritten(self, corpus):
        fixed, _ = sanitize_scene_headings(DRAFT, corpus)
        assert "KAN arrives on campus for the first time." in fixed

    def test_a_script_already_in_the_brands_format_is_left_alone(self, corpus):
        already = "INT. HOSTEL ROOM — DAY\nHe unpacks.\n"
        assert sanitize_scene_headings(already, corpus) == (already, [])


class TestWhetherTheHeadingIsTrue:
    def test_an_outdoor_place_marked_indoors_is_reported(self):
        found = heading_findings(DRAFT)
        assert len(found) == 1
        assert "campus is outdoors" in found[0]["message"]
        assert found[0]["line"] == 2, "say which line, so it can be found"

    def test_an_indoor_place_marked_outdoors_is_reported(self):
        found = heading_findings("EXT. HOSTEL ROOM — NIGHT\nHe sleeps.\n")
        assert found and "room is indoors" in found[0]["message"]

    def test_a_building_may_be_either(self):
        """This corpus writes both INT. DINER and EXT. DINER, for the two sides
        of the same door."""
        assert heading_findings("INT. DINER — DAY\nEXT. DINER — LATER\n") == []

    def test_an_int_ext_heading_is_not_faulted(self):
        assert heading_findings("INT./EXT. CAR — NIGHT\nHe drives.\n") == []

    def test_the_hand_written_script_has_none_left(self, gold, corpus):
        """It had one — "INT. CAMPUS — AFTER CLASSES" — and that was a slip
        rather than a house style, so the fixture was corrected. A reference
        with an error in it teaches the error."""
        assert heading_findings(gold) == []
        assert sanitize_scene_headings(gold, corpus)[1] == []
