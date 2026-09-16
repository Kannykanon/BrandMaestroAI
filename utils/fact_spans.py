"""Facts from a product document, kept in the source's own words.

Everything else in the Brand Brain is a description of *how* the brand writes.
This module is the one exception, and it runs the other way: a handful of short
spans from the product document that the writer must reproduce exactly.

It exists because of a specific failure. A treatment said:

    He finds a group of more than seven men.

The copying gate reported that as nine consecutive words lifted from the source
and told the writer to "keep the FACT, discard the source's wording". The writer
obeyed, and wrote:

    He observes a congregation. Numerous individuals are present. They exceed seven.

The hand-written correct version keeps the fact in the source's words and throws
the source's sentence away:

    More than seven men. Seated. Drinking.

That is the distinction this module draws. "More than seven men" is not phrasing
anybody chose — it is what is true, and there is no other way to say it without
either losing the number or sounding like nobody. The sentence around it is
phrasing, and the writer still has to build that itself.

So: quantities, dates, money and the names attached to them are extracted as
spans, handed to the writer as must-keep, and exempted from the copying gate.
Nothing else from a product document is exempt, and no brand-voice document
contributes a span at all.
"""
from __future__ import annotations

import re

# A qualifier is part of the fact. "More than seven" and "seven" are different
# claims, and dropping the qualifier is the commonest way a draft quietly
# becomes untrue.
_QUALIFIERS = (
    r"(?:more|less|fewer|greater|no\s+more|no\s+less)\s+than|"
    r"(?:at\s+least|at\s+most|up\s+to|as\s+many\s+as|as\s+few\s+as)|"
    r"over|under|almost|nearly|about|around|approximately|roughly|"
    r"exactly|just|only|within|every|per"
)

# Ordinals are deliberately absent. "First" and "second" are almost always
# sequence ("the first time he calls"), not quantity, and extracting them fills
# the writer's must-keep list with words that carry nothing.
_NUMBER_WORDS = (
    r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|dozen"
)

# A number, with its currency or unit attached: 40%, $1,200, 7.5x, 2024, N5,000.
_NUMERIC = r"(?:[$£€₦¥]\s?)?\d[\d,.]*\s?(?:%|x|k|m|bn|st|nd|rd|th)?"

_ANCHOR = re.compile(
    rf"(?:\b(?:{_QUALIFIERS})\s+)?"
    rf"(?:{_NUMERIC}|\b(?:{_NUMBER_WORDS})\b)"
    rf"(?:\s*[-–—]\s*(?:{_NUMERIC}|\b(?:{_NUMBER_WORDS})\b))?",
    re.IGNORECASE,
)

# What the number counts. Taken from the words that follow it, because that is
# where the noun sits in English: "seven men", "40 retail clients", "$2m raised".
_TRAILING_STOP = frozenset({
    "and", "or", "but", "so", "then", "the", "a", "an", "of", "to", "in", "on",
    "at", "by", "for", "with", "from", "that", "which", "who", "is", "are",
    "was", "were", "be", "been", "will", "would", "can", "could", "may",
    "might", "must", "should", "have", "has", "had", "it", "he", "she", "they",
})

_NUMBER_WORD_SET = frozenset(_NUMBER_WORDS.split("|"))

_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")

# Text that is planning, not product fact. A treatment's own notes about how the
# story might develop are not claims the piece has to carry.
_PLANNING_LINE = re.compile(
    r"^\s*(?:note|notes|todo|tbd|internal|draft|status|version|wip)\b[:\-]", re.IGNORECASE
)

MAX_SPAN_WORDS = 8


def _following_noun(tail: str, limit: int = 3) -> str:
    """The words a number counts, from the text straight after it.

    The phrase ends at the first function word, not merely at a trailing one.
    "Part 1 of an ongoing series" gave "1 of an ongoing" while only the end of
    the span was trimmed: "of" ends the noun phrase, and everything after it
    belongs to another one.
    """
    kept = []
    for word in re.findall(r"[A-Za-z][A-Za-z'’\-]*|\S", tail)[:limit * 2]:
        if not _WORD.fullmatch(word):
            break                            # punctuation closes the phrase
        if word.lower() in _TRAILING_STOP:
            break
        if len(word) == 1:
            break                            # an initial: "5 p.m." is not "5 p"
        kept.append(word)
        if len(kept) >= limit:
            break
    return " ".join(kept)


def _clean(span: str) -> str:
    span = re.sub(r"\s+", " ", span).strip(" \t,;:—–-")
    words = span.split()
    while words and _WORD.fullmatch(words[-1] or "") and words[-1].lower() in _TRAILING_STOP:
        words.pop()
    return " ".join(words[:MAX_SPAN_WORDS])


def extract_fact_spans(source: str, limit: int = 20) -> list[str]:
    """Short spans of `source` that carry a fact and must survive verbatim.

    Ordered by where they appear, deduplicated case-insensitively. A span is a
    number — with its qualifier, currency and unit — plus the words it counts.
    """
    if not source:
        return []
    spans: list[str] = []
    seen: set[str] = set()
    for line in source.splitlines():
        if _PLANNING_LINE.match(line):
            continue
        for match in _ANCHOR.finditer(line):
            # The unit group may have eaten the space after the number, which
            # would leave the noun unreachable: "40,000 " then "naira".
            anchor = match.group(0)
            tail = line[match.end() - (len(anchor) - len(anchor.rstrip())):]
            anchor = anchor.rstrip()
            # Up to three following words, stopping at any punctuation that ends
            # the noun phrase. "seven men, seated" gives "seven men".
            noun = _following_noun(tail)
            span = _clean(f"{anchor} {noun}" if noun else anchor)
            if not span or not any(ch.isalnum() for ch in span):
                continue
            # A bare number with nothing attached is a page number or a scene
            # index, not a claim.
            if not _WORD.search(span):
                continue
            key = re.sub(r"[^a-z0-9]+", " ", span.lower()).strip()
            # "More than seven men" and "more than seven men encountered" are one
            # fact stated twice. Keep the first, shortest form: the writer needs
            # the claim, not every sentence the source wrapped around it.
            if any(key.startswith(k) or k.startswith(key) for k in seen):
                continue
            seen.add(key)
            spans.append(span)
            if len(spans) >= limit:
                return spans
    return spans


def render_fact_spans(spans: list[str]) -> str:
    """The must-keep block for the writer's prompt."""
    if not spans:
        return ""
    lines = "\n".join(f'  - "{span}"' for span in spans)
    return (
        "FACTS THAT MUST APPEAR EXACTLY AS WRITTEN\n"
        "These are the only words you may take from the source document. They are what is true, not\n"
        "somebody's phrasing, and there is no correct way to reword them — dropping a qualifier or\n"
        "softening a number makes the piece untrue. Build your own sentences around them.\n"
        f"{lines}\n"
        "Everything else in the source is briefing material: take the fact and write it yourself."
    )


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def missing_fact_spans(content: str, spans: list[str]) -> list[str]:
    """Which of `spans` the draft states, but no longer states correctly."""
    if not spans or not content:
        return []
    haystack = _normalise(content)
    missing = []
    for span in spans:
        needle = _normalise(span)
        if not needle or needle in haystack:
            continue
        # Reported only when the draft states the number in some other form.
        # That is the failure: the fact is here, restated so that it no longer
        # is one — "They exceed seven" for "more than seven men", "seven men"
        # for "more than seven men".
        #
        # A draft that does not state the number at all is left alone, even when
        # it writes about the same subject. Not every piece carries every fact
        # in its source, and a summary that says "a crowd was waiting" has made
        # an editorial choice. Blocking that would fail a blog for writing "four
        # in ten marketers" where its research said "about 40% of marketers" —
        # the tight-rule failure this whole change exists to undo.
        number = next((w for w in needle.split()
                       if re.search(r"\d", w) or w in _NUMBER_WORD_SET), "")
        if number and re.search(rf"\b{re.escape(number)}\b", haystack):
            missing.append(span)
    return missing
