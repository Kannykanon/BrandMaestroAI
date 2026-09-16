"""Telling someone their brand-voice upload is a brief.

Four screenplays and one story treatment were uploaded for one brand. The
treatment was tagged as brand voice, and two things followed: its section
titles ("ACT I — A LIFE ALMOST REBUILT") were offered to the writer as brand
assets it was permitted to use, and its explanatory register was measured into
the targets the writer aimed at. The generated script reused another story's
act titles and wrote "This initial joy is transient".

Nothing in the system could have known that was the wrong role for it. It can
say what the document looks like.
"""
from utils.voice_spec import looks_like_a_brief

TREATMENT = """# KANCITY

### Film / Limited Series Treatment — Based on True Events

## LOGLINE (draft — to be refined as more of the story is added)

A student loses his wallet and finds the city waiting for him.

## FORMAT & GENRE

- Limited series, six episodes
- Crime drama

## CHARACTER PROFILES

**EMK** — a fixer. Function more as an atmosphere than a person at this stage.

## NOTES FOR CONTINUATION

Individual members may be developed further as the story continues.
"""

SCREENPLAY = """JOHN WICK (2014)
DETAILED SCREENPLAY-STYLE RETELLING

NOTE: This is an original retelling.

ACT I — WHAT'S LEFT

INT. WICK HOME — DAY

John sits alone with his grief. His wife has recently died. A package arrives,
sent by her before she passed. Inside is a dog, and a note he reads once.

He does not cry. He feeds the dog. In the morning he drives to the station and
fills the tank, and three men watch him from the far pump.

JOHN
It's not for sale.

They take the car that night. They kill the dog. He breaks the basement floor
open with a hammer, and under it are guns, and coins, and the life he agreed to
leave behind.

STRUCTURAL LESSON

An ordinary life, interrupted.
"""


class TestWhatABriefLooksLike:
    def test_a_treatment_is_recognised(self):
        assert looks_like_a_brief(TREATMENT)

    def test_a_piece_of_the_brands_own_writing_is_not(self):
        assert not looks_like_a_brief(SCREENPLAY), (
            "warning on real brand-voice documents would train people to ignore the warning"
        )

    def test_headings_are_matched_as_written_not_as_a_fixed_list(self):
        """The real treatment's headings were "LOGLINE (draft — to be refined...)"
        and "CHARACTER PROFILES". An exact-match list saw neither."""
        assert looks_like_a_brief(TREATMENT.replace("## LOGLINE (draft — to be refined as more of the story is added)",
                                                    "## LOGLINE"))

    def test_one_signal_alone_is_not_enough(self):
        """A published piece with a Summary heading and a list is still a
        published piece."""
        published = SCREENPLAY + "\n\nSUMMARY\n\n- He goes back.\n- They come for him.\n"
        assert not looks_like_a_brief(published)

    def test_a_short_fragment_is_not_judged(self):
        assert not looks_like_a_brief("## LOGLINE\n\nA man loses his wallet.")
        assert not looks_like_a_brief("")
