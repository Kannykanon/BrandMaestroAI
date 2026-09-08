"""Deterministic enforcement checks.

Every rule is derived from the brand's own extracted profile rather than from
anything hardcoded, so the same checks apply to any brand's documents.

Grouped by what they answer:
  punctuation   marks this brand does or does not use
  mechanics     rates this brand writes at, versus rates this draft writes at
  placeholders  template text that was never filled in
  provenance    claims the source material does not support
  preflight     the above, run together before spending a model call
"""
from utils.enforcement.constants import (
    BRACKET_CONVENTION_MIN_RATE,
    MAX_ITERATIONS,
    MAX_VERBATIM_SPAN_WORDS,
    MECHANICS_MIN_RATE,
    MECHANICS_TOLERANCE,
    MIN_PHONE_DIGITS,
)
from utils.enforcement.mechanics import check_measured_mechanics
from utils.enforcement.placeholders import find_unfilled_placeholders
from utils.enforcement.preflight import run_preflight_checks
from utils.enforcement.provenance import (
    find_altered_quotations,
    find_extractive_spans,
    find_ungrounded_contact_details,
    find_unverified_quote_attributions,
)
from utils.enforcement.punctuation import (
    mark_is_banned,
    measured_rate_for,
    sanitize_banned_punctuation,
)
from utils.enforcement.text import find_excerpt

__all__ = [
    "BRACKET_CONVENTION_MIN_RATE",
    "MAX_ITERATIONS",
    "MAX_VERBATIM_SPAN_WORDS",
    "MECHANICS_MIN_RATE",
    "MECHANICS_TOLERANCE",
    "MIN_PHONE_DIGITS",
    "check_measured_mechanics",
    "find_altered_quotations",
    "find_excerpt",
    "find_extractive_spans",
    "find_unfilled_placeholders",
    "find_ungrounded_contact_details",
    "find_unverified_quote_attributions",
    "mark_is_banned",
    "measured_rate_for",
    "run_preflight_checks",
    "sanitize_banned_punctuation",
]
