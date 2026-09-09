"""Tests for the measured-mechanics check.

The register cases come from a real generation. A documentary distributor whose
corpus runs 0.9 nominalisations per 100 words produced a press release at 6.1 —
"these represent a diagnostic failure to prioritize the integrity of effort over
immediate, superficial gratification" — and every check passed it, because the
block counted punctuation, capitals and sentence length and nothing about word
choice. Register is the most recognisable part of a voice, and it was unmeasured.
"""
import pytest

from utils.enforcement import check_measured_mechanics


# The rates a plain, concrete corpus actually produces.
PLAIN_BRAND = """# MEASURED MECHANICS
Counted directly from this brand's uploaded documents.
- exclamation_marks_per_100_words: 0.3
- question_marks_per_100_words: 0.3
- emoji_per_100_words: 0.0
- all_caps_words_per_100_words: 3.5
- mean_words_per_sentence: 14.4
- nominalisations_per_100_words: 0.9
- four_plus_syllable_words_per_100_words: 3.3
- mean_word_length: 4.5
- contractions_per_100_words: 4.0
- corpus_size_words: 1694
"""

# ~90 words of the register the brand does not write in.
CONSULTANCY_DRIFT = (
    "Our approach meticulously examines the presentation. We depict its "
    "distractions not as simple laziness. Instead, these represent a diagnostic "
    "failure to prioritize the integrity of effort over immediate, superficial "
    "gratification. This calculated persistence challenges the pervasive "
    "assumption that velocity alone equates to success. It demonstrates a "
    "superior understanding of the situation. This discerning focus unpacks the "
    "enduring cost of superficiality, and it culminates in a lasting "
    "humiliation. This is a testament to our commitment to substantive "
    "storytelling, delivering profound actionable insight and genuine "
    "engagement. We are architects of meaning."
)

# ~90 words of the register it does write in.
PLAIN_DRAFT = (
    "He stops for a nap. He eats some grapes. He gets in the water. That's not "
    "laziness; it's a hare who thinks he's got time. We've seen 3 films try this "
    "and 2 of them made the tortoise dull, which is the easy mistake. Slow is "
    "not the same as boring. The tortoise wants to win and knows exactly how "
    "long it will take him, and that's a plan, not a personality. We'd rather "
    "make the plan the story. It runs 11 minutes. You'll know by minute 2 "
    "whether it works."
)


class TestRegisterIsMeasured:
    def test_consultancy_drift_is_flagged(self):
        failures = check_measured_mechanics(CONSULTANCY_DRIFT, PLAIN_BRAND)
        flagged = " ".join(f["message"] for f in failures)
        assert "nominalisations" in flagged, (
            "abstraction went unmeasured — this is the failure the check exists for"
        )

    def test_the_brands_own_register_passes(self):
        assert check_measured_mechanics(PLAIN_DRAFT, PLAIN_BRAND) == [], (
            "plain concrete copy was flagged against a plain concrete brand"
        )

    def test_feedback_names_the_number_and_the_target(self):
        failures = check_measured_mechanics(CONSULTANCY_DRIFT, PLAIN_BRAND)
        message = next(f["message"] for f in failures if "nominalisations" in f["message"])
        assert "0.9" in message and "per 100 words" in message, (
            "the writer needs the target, not just a complaint"
        )


class TestSmallSampleNoise:
    """A ratio on a handful of events is not evidence.

    One of the brand's own documents failed its own corpus at 2x on six words
    that were not abstractions at all — audience, citation, competition,
    decompression, direction, sentence. At 415 words a rate of 0.84 predicts
    about 3; finding 7 is ordinary variance. So a failure needs the ratio and a
    real excess behind it.
    """

    def test_a_ratio_on_few_events_does_not_fail(self):
        # The real case: the six words that failed, once each, in a draft long
        # enough that 0.9 per 100 words predicts about 4. Seven is roughly twice
        # the rate and three more than predicted — over tolerance, under the
        # excess floor, so not evidence of anything.
        plain = "the boat is small and the water is cold and we go out at dawn "
        draft = (
            "The jury citation named the competition and the direction. "
            "She had a decompression injury. The audience stayed for the sentence. "
            + plain * 28
        )
        words = len(draft.split())
        assert 350 < words < 450, f"fixture drifted to {words} words"
        assert check_measured_mechanics(draft, PLAIN_BRAND) == [], (
            "flagged a small-sample fluctuation as a voice failure"
        )

    def test_a_large_excess_still_fails(self):
        assert check_measured_mechanics(CONSULTANCY_DRIFT * 2, PLAIN_BRAND), (
            "a sustained excess must still fail"
        )

    def test_short_content_is_still_measured(self):
        """
        A trailer is about 150 words, and a flat excess floor of 5 switched this
        check off at that length: the draft below ran 4.6x the brand's
        nominalisation rate and cleared every gate at 8.8/10, because 6
        occurrences against a predicted 1.3 fell less than one word short of the
        floor. Verbatim from the generation that exposed it.
        """
        trailer = (
            "the Tortoise and the Hare: You thought you knew the story. "
            "The classic moral of the Tortoise and the Hare often misses a deeper "
            "truth behind the finish line. We challenge this common perception: "
            "the real race isn't about speed, but self-mastery. "
            "One competitor was blessed with great natural swiftness; the other "
            "possessed a firm inner resolve. Our narrative dissects the hare's "
            "undoing, revealing it as a direct consequence of his profound hubris "
            "and akrasia. His constant distraction proved a greater obstacle than "
            "any rival. Common wisdom dictates raw speed guarantees victory. This "
            "film validates the tortoise's steady discipline and consistent effort "
            "as the true metrics for success. This isn't merely a race of physical "
            "prowess. It's a profound exploration of character. True victory is "
            "forged through internal strength, not just external pace. This new "
            "film reveals the overlooked lessons of focus versus distraction, and "
            "it's arriving soon. Deeper truths. Real insights."
        )
        words = len(trailer.split())
        assert 140 < words < 175, f"fixture drifted to {words} words"
        failures = check_measured_mechanics(trailer, PLAIN_BRAND)
        assert any("nominalisation" in f["message"] for f in failures), (
            "register drift went unmeasured on trailer-length content"
        )

    def test_the_short_sample_bar_scales_with_the_prediction(self):
        """
        The floor and the scaled bar have to disagree in the right direction: the
        bar must be looser than a flat 5 on short drafts and tighter on long
        ones, or one of the two cases regresses.
        """
        long_plain = "the boat is small and the water is cold and we go out at dawn "
        # Same three-abstraction opening, in a draft long enough that the brand's
        # own rate predicts about as many. Ordinary variance, must stay quiet.
        near_rate = (
            "The jury citation named the competition and the direction. "
            "She had a decompression injury. The audience stayed for the sentence. "
            + long_plain * 28
        )
        assert check_measured_mechanics(near_rate, PLAIN_BRAND) == [], (
            "a long draft close to the brand's own rate must not fail"
        )


class TestExistingChecksStillWork:
    @pytest.mark.parametrize("label,draft,expect", [
        ("shouting well above the brand's rate",
         ("THIS IS URGENT AND IMPORTANT NEWS TODAY FROM THE COMPANY ABOUT "
          "EVERYTHING WE HAVE DONE THIS YEAR AND NEXT YEAR ALSO SOON " * 2)
         + " a few plain words to dilute it slightly", True),
        ("no exclamation marks at all, against a brand that barely uses them",
         PLAIN_DRAFT, False),
    ])
    def test_punctuation_and_caps(self, label, draft, expect):
        assert bool(check_measured_mechanics(draft, PLAIN_BRAND)) is expect, label

    def test_no_measured_section_means_no_opinion(self):
        assert check_measured_mechanics(CONSULTANCY_DRIFT, "# BRAND VOICE\nPlain.") == []
