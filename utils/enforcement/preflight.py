"""Deterministic checks run before the Enforcer spends a model call.

Each rule here is mechanical and unambiguous once the brand's own habits are
known, so it gets a guaranteed answer rather than a probabilistic one.
"""
import re

from utils.brand_profile import brand_prose_section
from utils.enforcement.mechanics import check_measured_mechanics
from utils.enforcement.placeholders import find_unfilled_placeholders
from utils.enforcement.punctuation import mark_is_banned, measured_rate_for
from utils.enforcement.text import find_excerpt


def run_preflight_checks(content: str, metrics: str) -> list[dict]:
    """Run deterministic rule checks to catch strict anti-patterns before invoking the LLM.

    Each failure carries an "excerpt" of the offending text (not just the rule
    name) so the writer's revision prompt can find and fix the exact passage.
    """
    failures = []

    # 1. Check Punctuation
    rules = brand_prose_section(metrics, "PUNCTUATION HABITS")
    if rules:
        if mark_is_banned(rules, ("exclamation",), measured_rate_for(metrics, ("exclamation",))) and "!" in content:
            failures.append({
                "message": "Brand avoids exclamation marks (!), but they were found.",
                "excerpt": find_excerpt(content, "!"),
            })
        if mark_is_banned(rules, ("em dash", "em-dash", "emdash")) and ("—" in content or "--" in content):
            failures.append({
                "message": "Brand avoids em-dashes (— or --), but they were found.",
                "excerpt": find_excerpt(content, ["—", "--"]),
            })
        if mark_is_banned(rules, ("semicolon",)) and ";" in content:
            failures.append({
                "message": "Brand avoids semicolons (;), but they were found.",
                "excerpt": find_excerpt(content, ";"),
            })
        if mark_is_banned(rules, ("ellipsis", "ellipses")) and ("..." in content or "…" in content):
            failures.append({
                "message": "Brand avoids ellipses (...), but they were found.",
                "excerpt": find_excerpt(content, ["...", "…"]),
            })

    # 1b. Check measured mechanics — amplification, not just absence.
    # The punctuation rules above catch a mark the brand never uses. They say
    # nothing about a mark the brand DOES use being used three times as often,
    # which is its own voice failure: a brand at 7.4 exclamation marks per 100
    # words generated copy at 21.4 and every qualitative rule still "passed".
    failures.extend(check_measured_mechanics(content, metrics))

    # 1c. Unfilled template placeholders. Deterministic because "[Press Contact
    # Name]" is unambiguous once the brand's own bracket convention is known.
    failures.extend(find_unfilled_placeholders(content, metrics))

    # 2. Check Question Usage
    q_section = brand_prose_section(metrics, "QUESTION USAGE")
    if q_section:
        if mark_is_banned(
            q_section, ("question",), measured_rate_for(metrics, ("question",))
        ) and "?" in content:
            failures.append({
                "message": "Brand strictly avoids questions, but a question mark (?) was found.",
                "excerpt": find_excerpt(content, "?"),
            })

    return failures
