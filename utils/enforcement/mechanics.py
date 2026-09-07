"""Surface mechanics measured against the brand's own rates.

The punctuation rules catch a mark a brand never uses. This catches a mark the
brand does use being used several times as often, which is its own voice
failure and passes every qualitative rule.
"""
import re

from utils.brand_profile import measured_mechanics_section
from utils.enforcement.constants import MECHANICS_MIN_RATE, MECHANICS_TOLERANCE


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
        label = key.replace("_per_100_words", "").replace("_", " ")
        failures.append({
            "message": (
                f"Brand uses {label} at {target:.1f} per 100 words; this draft uses "
                f"{actual:.1f} per 100 words ({actual / target:.1f}x). Match the "
                f"brand's rate rather than amplifying it — reduce to roughly "
                f"{target:.1f} per 100 words."
            ),
            "excerpt": "",
        })
    return failures
