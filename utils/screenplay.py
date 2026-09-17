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
"INT. UNIVERSITY CAMPUS" is wrong however the brand punctuates it, and wrong in
the same mechanical way: no judgement is needed to know which side of a door a
campus is on. It is corrected too.

The lexicon that decides this is deliberately short, and everything ambiguous
is left alone. A diner, a hotel or a home is legitimately INT. or EXT. depending
on which side of its door the scene is, and this corpus uses both prefixes for
the same building. Only places that are one thing anywhere — a street, a
campus, a bedroom — are touched.

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


def _true_prefix(prefix: str, place: str) -> str:
    """The prefix this place actually takes, or the one given when it is open."""
    upper = prefix.upper().rstrip(".")
    if "/" in upper or upper in ("I/E", "EST"):
        return prefix  # a scene that spans both, or an establishing shot
    words = set(re.findall(r"[a-z]+", place.lower()))
    outside = any(w in words or w in place.lower() for w in _EXTERIOR_ONLY)
    inside = any(w in words or w in place.lower() for w in _INTERIOR_ONLY)
    if outside and not inside:
        return "EXT."
    if inside and not outside:
        return "INT."
    return prefix


def sanitize_scene_headings(content: str, corpus: str) -> tuple:
    """Put headings into the brand's format, and make them true.

    Both halves are mechanical — the separator is the brand's own habit, and
    which side of a door a campus is on is not a matter of taste — so both are
    done here rather than sent back for a revision round. Returns the content
    and a list of what changed.
    """
    wanted = brand_separator(corpus) or DEFAULT_SEPARATOR
    fixes, lines = [], content.splitlines()

    for index, line in enumerate(lines):
        stripped = line.strip()
        match = HEADING.match(stripped)
        if not match:
            continue
        prefix, rest = match.group("prefix"), match.group("rest")

        for separator in _SEPARATORS:
            if separator == wanted or separator.strip() == wanted:
                continue
            if separator in rest:
                rest = rest.replace(separator, f" {wanted} ", 1)
                break

        place = re.split(re.escape(wanted) + r"|[—–,]| - ", rest, maxsplit=1)[0]
        prefix = _true_prefix(prefix, place)

        fixed = re.sub(r"\s{2,}", " ", f"{prefix.upper().rstrip('.')}. {rest}").strip()
        if fixed != stripped:
            fixes.append(f"{stripped!r} -> {fixed!r}")
            lines[index] = fixed

    return ("\n".join(lines) if fixes else content), fixes


def heading_findings(content: str) -> list:
    """Headings whose prefix contradicts the place they name.

    The sanitiser corrects these, so on content that has been through it this
    returns nothing. It stays separate because a check that only looks is worth
    having on its own: it is how the harness proves a script's headings are
    sound without rewriting it first.
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
