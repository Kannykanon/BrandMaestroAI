"""A name spelled two ways in one draft.

The treatment introduces the character as EMK and never writes Emk. A draft
introduced him as EMK and then wrote Emk three times. The voice pass noticed,
spent its only finding on it, and approved the draft at 9.8 anyway — so both
spellings shipped.

Which capitals a name takes is not a judgement call, so it is corrected in code
like the scene-heading separator rather than sent back to the writer.

The rule is not "capitalise names". A screenplay capitalises a character on
first appearance and writes them normally afterwards, and the treatment follows
that: Kan twelve times, KAN twice. EMK five times, Emk never. A name the source
never writes in mixed case is an initialism; a name it usually does is not.
"""
import pathlib

import pytest

from utils.screenplay import initialisms, sanitize_character_names

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "gold" / "kancity"
SOURCE = (FIXTURES / "brief.txt").read_text(encoding="utf-8")
GOLD = (FIXTURES / "gold.txt").read_text(encoding="utf-8")


class TestWhichNamesAreInitialisms:
    def test_a_name_the_source_only_shouts_is_one(self):
        assert "EMK" in initialisms(SOURCE)

    def test_a_name_the_source_usually_writes_normally_is_not(self):
        """Kan appears as KAN in the synopsis heading and as Kan throughout.
        Forcing every mention to capitals would be wrong screenplay style and
        would rewrite most of the script."""
        assert "KAN" not in initialisms(SOURCE)


class TestTheFix:
    DRAFT = ("One man sits among them. His name is EMK.\n"
             "They greet the group, and Emk specifically.\n"
             "Emk's demeanor softens.\n"
             "Emk gives Kan his personal number.\n")

    def test_the_draft_is_made_to_agree_with_itself(self):
        fixed, notes = sanitize_character_names(self.DRAFT, SOURCE)
        assert "Emk" not in fixed
        assert fixed.count("EMK") == 4
        assert notes == ["Emk -> EMK (3x)"]

    def test_a_possessive_is_still_a_mention(self):
        fixed, _ = sanitize_character_names(self.DRAFT, SOURCE)
        assert "EMK's demeanor" in fixed

    def test_kan_is_left_in_normal_case(self):
        fixed, _ = sanitize_character_names(self.DRAFT, SOURCE)
        assert "Kan his personal number" in fixed


class TestWhatItMustNotTouch:
    def test_the_hand_written_script_is_unchanged(self):
        fixed, notes = sanitize_character_names(GOLD, SOURCE)
        assert fixed == GOLD and notes == []

    def test_a_draft_that_already_agrees_with_itself_is_unchanged(self):
        one_way = "His name is EMK. EMK gives Kan his number."
        assert sanitize_character_names(one_way, SOURCE) == (one_way, [])

    def test_the_documents_own_section_labels_are_not_names(self):
        """The source shouts FORMAT, GENRE and CHARACTER PROFILES in its
        headings. None of them are evidence about anything until a draft has
        shown it cannot keep one spelling of the word."""
        prose = "The format is a screenplay. The genre is crime."
        assert sanitize_character_names(prose, SOURCE) == (prose, [])

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_nothing_to_do_is_not_an_error(self, empty):
        assert sanitize_character_names(empty, SOURCE) == (empty, [])
        assert sanitize_character_names(self.__class__.__doc__ or "x", empty)[1] == []


def test_the_enforcer_applies_it_for_narrative():
    import inspect

    from nodes import enforcer

    source = inspect.getsource(enforcer.enforcer_node)
    assert "sanitize_character_names" in source
    assert source.index("is_narrative") < source.index("sanitize_character_names")
