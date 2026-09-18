"""What a brand's punctuation is, versus what the synthesis says it is.

A run spent every one of its rounds on this:

    DETERMINISTIC ANTI-PATTERN DETECTED — Brand avoids em-dashes (— or --),
    but they were found. Offending text: "...EXT. CAMPUS GROUNDS — DAY..."

Two things were wrong at once. The synthesised PUNCTUATION HABITS said the
brand avoids em-dashes while its own scripts contain 87 of them. And the
em-dash in that heading had just been put there by the scene-heading sanitiser,
which normalises headings to the separator the brand actually uses — so one
part of the system was inserting a character another part refused, for fourteen
rounds, until the budget ran out.

The mechanism to prevent this already existed and covered two marks out of six:
where a mark is counted, the count settles it, because the synthesis is a
description and the corpus is the thing described.
"""
import pytest

from tests.gold_pairs import discover
from utils.enforcement import run_preflight_checks, sanitize_banned_punctuation
from utils.enforcement.punctuation import mark_is_banned, measured_rate_for
from utils.screenplay import sanitize_scene_headings

CLAIMS_OTHERWISE = """
# PUNCTUATION HABITS
Em dashes are consistently absent. Semicolons and ellipses are avoided entirely.
"""


@pytest.fixture(scope="module")
def pair():
    return discover()[0]


@pytest.fixture(scope="module")
def brain(pair):
    return pair.brain + CLAIMS_OTHERWISE


class TestTheCountSettlesIt:
    def test_the_marks_a_brand_uses_are_counted(self, brain):
        for alias in ("em dash", "semicolon", "ellipsis", "exclamation", "question"):
            assert measured_rate_for(brain, (alias,)) is not None, f"{alias} is not counted"

    def test_a_mark_the_brand_uses_is_not_banned_by_a_sentence_saying_it_is(self, brain):
        assert measured_rate_for(brain, ("em dash",)) > 0
        assert not mark_is_banned(brain, ("em dash", "em-dash", "emdash"),
                                  measured_rate_for(brain, ("em dash",)))

    def test_a_mark_the_brand_never_uses_is_still_banned(self):
        assert mark_is_banned("Em dashes are absent.", ("em dash",), 0.0)

    def test_an_uncounted_mark_still_falls_back_to_the_prose(self):
        """Nothing counts colons, so the description is all there is."""
        assert measured_rate_for("# MEASURED MECHANICS\n- words: 10\n", ("colon",)) is None


class TestTheSanitisersNoLongerFightEachOther:
    DRAFT = "EXT. CAMPUS GROUNDS, DAY\nKan steps onto the campus.\nHe feels a buzz of excitement.\n"

    def _both(self, pair, brain):
        content, _ = sanitize_banned_punctuation(self.DRAFT, brain)
        content, _ = sanitize_scene_headings(content, "\n".join(pair.voice))
        return content

    def test_the_heading_keeps_the_separator_the_brand_uses(self, pair, brain):
        assert "EXT. CAMPUS GROUNDS — DAY" in self._both(pair, brain)

    def test_and_preflight_does_not_then_refuse_it(self, pair, brain):
        """Fourteen rounds were spent refusing a character the system had just
        inserted itself."""
        assert run_preflight_checks(self._both(pair, brain), brain) == []

    def test_the_hand_written_script_survives_both(self, pair, brain):
        content, _ = sanitize_banned_punctuation(pair.gold, brain)
        content, fixes = sanitize_scene_headings(content, "\n".join(pair.voice))
        assert fixes == [] and content == pair.gold
        assert run_preflight_checks(content, brain) == []
