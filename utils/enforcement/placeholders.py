"""Unfilled template placeholders left in generated copy."""
import re

from utils.brand_profile import measured_mechanics_section
from utils.enforcement.constants import BRACKET_CONVENTION_MIN_RATE
from utils.enforcement.text import find_excerpt


# Placeholders that are never a deliberate stylistic choice, whatever the brand.
ALWAYS_PLACEHOLDER = re.compile(
    r"(lorem ipsum|\bTODO\b|\bTBC\b|\bXXX+\b|\{\{[^}]{1,60}\}\}"
    r"|\[\s*(?:insert|add|your|placeholder|tbd|tk)\b[^\]]{0,60}\])",
    re.IGNORECASE,
)
# Bracketed slots, which some content types use as a real convention.
BRACKET_SLOT = re.compile(r"\[[^\]]{1,60}\]")


def find_unfilled_placeholders(content: str, metrics: str) -> list[dict]:
    """Catch template placeholders that were never filled in.

    An approved press release shipped with "[cast TBD]", "[Press Contact Name]",
    "[Press Email Address]" and "[Press Phone Number]" still in it and scored
    9.0/10, because nothing looked for them.

    Brackets alone cannot be the rule: the same studio's ad copy really does
    write "[DATE]" on an end card, and its caption archive tags entries with
    "[Launch post]". So bracketed slots are only failed when this content type's
    own corpus shows no such convention. Placeholders that are never intentional
    — lorem ipsum, TODO, {{mustache}}, "[insert ...]" — always fail.
    """
    failures = []

    for match in ALWAYS_PLACEHOLDER.finditer(content):
        failures.append({
            "message": (
                f"Unfilled placeholder {match.group(0)!r} left in the content. "
                "Replace it with real material or remove the passage."
            ),
            "excerpt": find_excerpt(content, match.group(0)),
        })

    section = measured_mechanics_section(metrics)

    def measured(key):
        if not section:
            return None
        found = re.search(rf"{key}:\s*([0-9.]+)", section)
        return float(found.group(1)) if found else None

    annotation_rate = measured("bracket_placeholders_per_100_words")
    inline_rate = measured("inline_bracket_placeholders_per_100_words")

    # Position decides which rate applies. A bracket at the end of a line is an
    # annotation; one with copy after it on the same line is an insertion into
    # the prose. A brand can genuinely do the first while never doing the second
    # — Meridian's caption archive tags entries "[Launch post]" and its ad copy
    # ends on "[DATE]", but it never writes "[so] sorry in advance" mid-sentence.
    seen = {f["excerpt"] for f in failures}
    for line in content.splitlines():
        for match in BRACKET_SLOT.finditer(line):
            token = match.group(0)
            is_inline = bool(line[match.end():].strip())
            rate = inline_rate if is_inline else annotation_rate
            # Without a measured rate we cannot tell convention from defect, so
            # stay quiet rather than fail a brand we have not measured.
            if rate is None or rate >= BRACKET_CONVENTION_MIN_RATE:
                continue
            if any(token in f["message"] for f in failures):
                continue
            excerpt = find_excerpt(content, token)
            if excerpt in seen:
                continue
            seen.add(excerpt)
            where = (
                "mid-sentence, with copy continuing after it"
                if is_inline else "as a standalone annotation"
            )
            failures.append({
                "message": (
                    f"Unfilled placeholder {token!r} left in the content, {where}. "
                    f"This content type's own material uses brackets in that position "
                    f"at {rate} per 100 words, so this is template text rather than "
                    "house style. Write the real wording or remove it."
                ),
                "excerpt": excerpt,
            })

    return failures[:6]
