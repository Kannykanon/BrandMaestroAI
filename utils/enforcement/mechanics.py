"""Surface mechanics measured against the brand's own rates.

The punctuation rules catch a mark a brand never uses. This catches a mark the
brand does use being used several times as often, which is its own voice
failure and passes every qualitative rule.
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

# Kept in step with brand_metrics, which counts the same two properties over the
# corpus. Both sides have to agree or the comparison is meaningless.
_NOMINALISATION_RE = re.compile(
    r"\b\w{4,}(?:tion|sion|ment|ity|ance|ence|ness|ism|ivity)\b", re.I
)


def _syllables(word: str) -> int:
    """Rough syllable count: vowel groups, minimum one."""
    return max(1, len(re.findall(r"[aeiouy]+", word.lower())))


def check_measured_mechanics(content: str, metrics: str) -> list[dict]:
    """Flag surface mechanics used far more heavily than the brand's own corpus."""
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
        # Same direction as the rules above: over-use fails. Writing more
        # abstractly than the brand does is a voice failure in exactly the way
        # shouting three times as often is.
        "nominalisations_per_100_words": rate(
            len(_NOMINALISATION_RE.findall(content))
        ),
        "four_plus_syllable_words_per_100_words": rate(
            sum(1 for w in words if _syllables(w) >= 4)
        ),
    }

    failures = []
    for key, actual in observed.items():
        try:
            target = float(targets.get(key, ""))
        except ValueError:
            continue
        if target < MECHANICS_MIN_RATE or actual <= max(target * MECHANICS_TOLERANCE,
                                                        MECHANICS_MIN_RATE):
            continue
        # The ratio is over tolerance; check it rests on enough events to mean
        # something. Rate times length gives what the brand's own habit predicts
        # for a draft this long, and the excess over that is what has to be real.
        # The bar scales with the square root of the prediction, so it stays
        # strict on long drafts without switching itself off on short ones.
        expected = target * len(words) / 100.0
        excess = (actual * len(words) / 100.0) - expected
        if excess < max(MECHANICS_MIN_EXCESS,
                        MECHANICS_EXCESS_SIGMAS * math.sqrt(expected)):
            continue
        label = key.replace("_per_100_words", "").replace("_", " ")
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
    return failures
