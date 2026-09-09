"""Surface mechanics measured against the brand's own rates.

The punctuation rules catch a mark a brand never uses. This catches the two
failures those rules cannot see, both of which pass every qualitative check: a
mark the brand does use being used several times as often, and a habit the
brand has being dropped almost entirely. Amplifying a voice and flattening one
are the same error in opposite directions.
"""
import math
import re

from utils.brand_profile import measured_mechanics_section
from utils.enforcement.constants import (
    MECHANICS_EXCESS_SIGMAS,
    MECHANICS_MIN_EXCESS,
    MECHANICS_MIN_RATE,
    MECHANICS_TOLERANCE,
)

# Kept in step with brand_metrics, which counts these same properties over the
# corpus. Both sides have to agree or the comparison is meaningless.
_NOMINALISATION_RE = re.compile(
    r"\b\w{4,}(?:tion|sion|ment|ity|ance|ence|ness|ism|ivity)\b", re.I
)

_CONTRACTION_RE = re.compile(r"\b\w+['\u2019](?:t|s|re|ve|ll|d|m)\b", re.I)


# Which measured rates are also checked for UNDER-use.
#
# Only features distributed through the prose. all-caps words and emoji are
# deliberately excluded: both concentrate in particular sections — a trailer
# sheet puts its capitals in card blocks, a caption archive puts its emoji in
# sign-offs — so a legitimate draft covering only one part of a document sits
# far below the whole document's rate without anything being wrong with it.
# That asymmetry is already documented for the over-use direction below; under
# it, the same concentration turns every partial draft into a false positive.
UNDER_USED_KEYS = frozenset({
    "exclamation_marks_per_100_words",
    "question_marks_per_100_words",
    "nominalisations_per_100_words",
    "four_plus_syllable_words_per_100_words",
    "contractions_per_100_words",
})


def _syllables(word: str) -> int:
    """Rough syllable count: vowel groups, minimum one."""
    return max(1, len(re.findall(r"[aeiouy]+", word.lower())))


def check_measured_mechanics(content: str, metrics: str) -> list[dict]:
    """Flag surface mechanics that depart from the brand's own corpus rates.

    Both directions, against the same evidence bar — see UNDER_USED_KEYS for
    which features are judged for under-use and why the other two are not.
    """
    section = measured_mechanics_section(metrics)
    if not section:
        return []

    targets = dict(re.findall(r"-\s*([a-z0-9_]+):\s*([0-9.]+)", section))
    words = re.findall(r"[A-Za-z0-9']+", content)
    if len(words) < 20:
        return []

    def rate(n):
        return 100.0 * n / len(words)

    emoji_re = re.compile(
        "[\U0001F300-\U0001FAFF\U00002600-\U000027BF"
        "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF]"
    )
    observed = {
        "exclamation_marks_per_100_words": rate(content.count("!")),
        "question_marks_per_100_words": rate(content.count("?")),
        "emoji_per_100_words": rate(len(emoji_re.findall(content))),
        "all_caps_words_per_100_words": rate(
            sum(1 for w in words if len(w) > 2 and w.isupper())
        ),
        # Register. Everything above is punctuation and capitals; none of it
        # notices a brand's plain voice being rewritten as consultancy prose.
        # A distributor whose corpus runs 0.89 nominalisations per 100 words
        # produced copy at 6.17 — nearly seven times its own rate — and passed
        # every check here, because none of them looked at word choice.
        #
        # Writing more abstractly than the brand does is a voice failure in
        # exactly the way shouting three times as often is — and writing more
        # plainly than a formal brand does is the same failure inverted, which
        # is why this one is judged in both directions.
        "nominalisations_per_100_words": rate(
            len(_NOMINALISATION_RE.findall(content))
        ),
        "four_plus_syllable_words_per_100_words": rate(
            sum(1 for w in words if _syllables(w) >= 4)
        ),
        # Under-use is what this one is here for. A brand at 3.8
        # contractions per 100 words whose draft has none has not written a
        # neutral piece, it has written a formal one — and formality creep is
        # how a plain voice goes flat. Nothing else measured here notices it.
        "contractions_per_100_words": rate(len(_CONTRACTION_RE.findall(content))),
    }

    failures = []
    for key, actual in observed.items():
        try:
            target = float(targets.get(key, ""))
        except ValueError:
            continue
        if target < MECHANICS_MIN_RATE:
            continue

        # What the brand's own habit predicts for a draft this long, and what
        # this draft actually did. Both directions are judged against the same
        # bar: it scales with the square root of the prediction, so it stays
        # strict on long drafts without switching itself off on short ones.
        expected = target * len(words) / 100.0
        seen = actual * len(words) / 100.0
        bar = max(MECHANICS_MIN_EXCESS,
                  MECHANICS_EXCESS_SIGMAS * math.sqrt(expected))
        label = key.replace("_per_100_words", "").replace("_", " ")

        if actual > max(target * MECHANICS_TOLERANCE, MECHANICS_MIN_RATE):
            if seen - expected < bar:
                continue
            # A rate has a numerator and a denominator, and "use it less" only
            # addresses the numerator. Where the brand concentrates a feature in
            # particular sections — a trailer copy sheet puts all-caps in its card
            # blocks and almost none in its sound notes, restrictions and usage
            # sections — a draft containing only the heavily marked section cannot
            # reach the whole document's rate however much it trims. Its caps are
            # correct; it is missing the sections that dilute them. Saying only
            # "reduce" sent the writer round every remaining iteration deleting
            # cards it was right to include.
            failures.append({
                "message": (
                    f"Brand uses {label} at {target:.1f} per 100 words; this draft uses "
                    f"{actual:.1f} per 100 words ({actual / target:.1f}x). Bring it to "
                    f"roughly {target:.1f} per 100 words. This is a rate, so there are "
                    f"two ways to do that: use the feature less, or — if the brand's "
                    f"documents of this kind also contain sections where it barely "
                    f"appears — write those sections too. If your draft reproduces only "
                    f"the most heavily marked part of the document, the second is the "
                    f"fix and deleting content is not."
                ),
                "excerpt": "",
            })

        elif key in UNDER_USED_KEYS and actual * MECHANICS_TOLERANCE < target:
            if expected - seen < bar:
                continue
            # The brain tells the writer these are "targets, not maximums to
            # exceed: match the rate, do not amplify it. Writing well above these
            # rates is as wrong as writing below them" — and then only over-use
            # was ever checked, so the second half of that sentence was
            # unenforced. Observed live: a distributor whose corpus runs 3.8
            # contractions per 100 words shipped a trailer with zero, at 2.7x its
            # four-syllable rate, and the register check had nothing to say
            # because every count was below target rather than above it. Going
            # under is the more common drift, because the failure mode of a model
            # writing carefully is to write formally.
            failures.append({
                "message": (
                    f"Brand uses {label} at {target:.1f} per 100 words; this draft uses "
                    f"{actual:.1f} per 100 words — well under the brand's own rate. "
                    f"Bring it to roughly {target:.1f} per 100 words. Match the brand's "
                    f"habit rather than writing around it: this is what makes a draft "
                    f"read as careful and generic instead of as this brand. Do not pad "
                    f"the piece to reach the number — write the sentences the way the "
                    f"brand writes them, and the rate follows."
                ),
                "excerpt": "",
            })

    return failures
