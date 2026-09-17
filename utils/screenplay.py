"""Scene headings: the brand's format, and the places they claim.

Two different problems live in one line of a script.

The first is format, and it is the brand's to decide. This corpus writes
"INT. WICK HOME — DAY": prefix, place, em dash, time. A draft came back with
"INT. UNIVERSITY CAMPUS, DAY" — a comma. Nothing read it as wrong because
nothing was looking at headings at all, and it is not a judgement call: the
separator the brand uses is measurable, and a draft that uses another one is
simply inconsistent with its own script. That is fixed in code, the way banned
punctuation and unbranded capitals already are.

The second is whether the heading is true. A campus is outdoors, so
"INT. UNIVERSITY CAMPUS" is wrong however the brand punctuates it. But this is
reported rather than corrected, because the hand-written correct script for
this brand contains "INT. CAMPUS — AFTER CLASSES", and a rule that silently
rewrites the answer a person wrote by hand is a rule that has stopped being
evidence. It says what it found and leaves the decision where it belongs.

What neither can see is continuity — a scene headed INT. UNIVERSITY HOSTEL
whose action is the character leaving a group at a location across town. That
needs to know where people are, which is the judge's job, not a regex's.
"""
from __future__ import annotations

import re
from collections import Counter

HEADING = re.compile(
    r"^\s*(?P<prefix>INT\.?/EXT\.?|I/E|INT\.|EXT\.|EST\.)\s*(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)

# What separates the place from the time of day. The brand picks one; the draft
# should use the same one.
_SEPARATORS = ("—", "–", " - ", ", ", " -- ")
DEFAULT_SEPARATOR = "—"

# Places that are one thing or the other whatever else is in the name.
# Deliberately short: a diner, a hotel or a home is legitimately INT. or EXT.
# depending on which side of the door the scene is, and this corpus uses both
# for the same building.
_EXTERIOR_ONLY = (
    "campus", "street", "road", "gate", "gates", "park", "field", "yard",
    "premises", "beach", "alley", "highway", "courtyard", "car park",
    "roadside", "junction", "pavement", "sidewalk", "rooftop", "forest",
)
_INTERIOR_ONLY = (
    "room", "bedroom", "kitchen", "bathroom", "corridor", "hallway", "lobby",
    "basement", "stairwell", "classroom", "lecture hall", "cell", "ward",
)


def headings(text: str) -> list:
    """Every scene heading in a script, with the line it sits on."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = HEADING.match(line.strip())
        if match:
            found.append((number, line.strip(), match.group("prefix"), match.group("rest")))
    return found


def separator_used(text: str) -> str:
    """The separator this text's headings use, or "" when there is no pattern."""
    counts: Counter = Counter()
    for _, _, _, rest in headings(text):
        for separator in _SEPARATORS:
            if separator in rest:
                counts[separator.strip() or separator] += 1
                break
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def brand_separator(corpus: str) -> str:
    """What the brand's own scripts put between the place and the time."""
    return separator_used(corpus) or DEFAULT_SEPARATOR


def sanitize_scene_headings(content: str, corpus: str) -> tuple:
    """Rewrite headings that use a separator the brand does not.

    Mechanical, so it is done rather than asked for. Returns the content and a
    list of what changed.
    """
    wanted = brand_separator(corpus)
    if not wanted:
        return content, []

    fixes, lines = [], content.splitlines()
    for index, line in enumerate(lines):
        match = HEADING.match(line.strip())
        if not match:
            continue
        rest = match.group("rest")
        for separator in _SEPARATORS:
            if separator == wanted or separator.strip() == wanted:
                continue
            if separator in rest:
                fixed = f"{match.group('prefix').upper().rstrip('.')}. " + rest.replace(
                    separator, f" {wanted} ", 1
                )
                fixed = re.sub(r"\s{2,}", " ", fixed).strip()
                fixes.append(f"{line.strip()!r} -> {fixed!r}")
                lines[index] = fixed
                break
    return ("\n".join(lines) if fixes else content), fixes


def heading_findings(content: str) -> list:
    """Headings whose prefix contradicts the place they name.

    Advisory. The hand-written correct script for this brand opens a scene
    "INT. CAMPUS — AFTER CLASSES", and a check that rewrites that has stopped
    being evidence and started being an opinion.
    """
    findings = []
    for number, raw, prefix, rest in headings(content):
        place = re.split(r"[—–,]| - ", rest, maxsplit=1)[0].strip().lower()
        words = set(re.findall(r"[a-z]+", place))
        prefix_upper = prefix.upper()
        if prefix_upper.startswith("INT") and not prefix_upper.startswith("INT.?/"):
            hit = next((w for w in _EXTERIOR_ONLY if w in words or w in place), "")
            if hit and "/" not in prefix:
                findings.append({
                    "line": number, "heading": raw, "place": hit,
                    "message": f"line {number}: {raw!r} is marked INT. but a {hit} is outdoors",
                })
        elif prefix_upper.startswith("EXT"):
            hit = next((w for w in _INTERIOR_ONLY if w in words or w in place), "")
            if hit:
                findings.append({
                    "line": number, "heading": raw, "place": hit,
                    "message": f"line {number}: {raw!r} is marked EXT. but a {hit} is indoors",
                })
    return findings
