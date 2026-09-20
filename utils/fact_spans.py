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
spans and handed to the writer as must-keep. Nothing else from a product
document is, and no brand-voice document contributes a span at all.

Superlatives are here for the same reason, after a draft turned "the most
feared cult group in the state, and one of the deadliest in the country" into
"the deadliest in the state" — a different attribute, a dropped "one of" and a
different scope, in six words. A superlative's qualifier is part of it exactly
as a number's is.

These spans are not exempted from the copying gate and do not need to be: each
is short enough to clear it on its own. A claim that cannot be said in eight
words is phrasing, and phrasing is the writer's job.
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
# The digits never end on a separator: "more than 14,000." kept the sentence's
# full stop inside the fact, and the writer was handed a must-keep span it had
# to end a sentence with to satisfy.
#
# The unit must not run into a word. Unguarded, the "m" of "minutes" read as the
# unit for millions — "about 8 minutes" came back as the span "about 8 m" with
# "inutes" left over, and every duration and distance in a product document was
# extracted wrong. A script's product document had none; ad copy is mostly them.
_NUMERIC = r"(?:[$£€₦¥]\s?)?\d[\d,.]*(?<![.,])\s?(?:%|x|k|m|bn|st|nd|rd|th)?(?![A-Za-z])"

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

# Claims that are superlatives. A number is not the only kind of fact whose
# qualifier is part of it.
#
# A product document said the group was "widely regarded as the most feared cult
# group in the state, and one of the deadliest in the country". A draft wrote
# "It is the deadliest in the state" — three changes, each making it a claim the
# source does not make: a different attribute, a dropped "one of", a different
# scope. The hallucination gate caught it, and the judge then told the writer to
# use the source's phrasing, which is a nineteen-word clause the copying gate
# refuses. The writer spent a round trapped between them.
#
# Handing these over as must-keep spans up front is the same move this module
# already makes for "more than seven men", and for the same reason: there is no
# second correct wording. Both spans are short enough to clear the copying gate
# on their own, so nothing here widens what may be reproduced.
_SET_OF = r"(?:one|two|three|four|five|several|a\s+few)\s+of\s+the|among\s+the"

# Words ending in -est that are not superlatives. Without them "among the rest
# of them" and "one of the guest rooms" read as claims about being the most of
# something.
_NOT_SUPERLATIVE = (
    "rest|test|west|nest|guest|quest|chest|crest|priest|forest|honest|modest|"
    "earnest|interest|harvest|contest|protest|request|arrest|invest|digest|"
    "suggest|manifest|conquest|unrest|tempest|midwest|incest|behest|pest|vest|"
    "zest|jest|lest|wrest|attest|detest|divest|northwest|southwest"
)

# "the most" is required rather than a bare "most": "most people left" is a
# quantifier, not a claim to be the most of anything. A bare "the ...est" is not
# matched at all, because "the forest" is a wood.
_SUPERLATIVE = (
    rf"the\s+(?:most|least)\s+[A-Za-z]+"
    rf"|(?:{_SET_OF})\s+(?:most|least)\s+[A-Za-z]+"
    rf"|(?:{_SET_OF})\s+(?!(?:{_NOT_SUPERLATIVE})\b)[A-Za-z]+est\b"
)

# What it is the most of, and where. Both belong to the claim: a cult that is
# the most feared in the state is not the most feared in the country.
_NOT_A_NOUN = r"in|of|and|or|the|a|an|to|for|with|at|by|from|that|which|is|are|was|were"

_SUPERLATIVE_SPAN = re.compile(
    rf"(?:{_SUPERLATIVE})"
    rf"(?:\s+(?!(?:{_NOT_A_NOUN})\b)[A-Za-z][A-Za-z'’\-]*){{0,2}}"
    rf"(?:\s+(?:in|of)\s+(?:the\s+)?[A-Za-z][A-Za-z'’\-]*)?",
    re.IGNORECASE,
)

# The word whose presence says the draft is making this claim at all.
_SUPERLATIVE_WORD = re.compile(r"\b(?:most|least)\s+([A-Za-z]+)|\b([A-Za-z]+est)\b", re.I)

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

    def consider(span: str) -> bool:
        """Keep this span unless it is empty, bare, or already said. False when full."""
        if not span or not any(ch.isalnum() for ch in span):
            return True
        # A bare number with nothing attached is a page number or a scene
        # index, not a claim.
        if not _WORD.search(span):
            return True
        key = re.sub(r"[^a-z0-9]+", " ", span.lower()).strip()
        # "More than seven men" and "more than seven men encountered" are one
        # fact stated twice. Keep the first, shortest form: the writer needs
        # the claim, not every sentence the source wrapped around it.
        if any(key.startswith(k) or k.startswith(key) for k in seen):
            return True
        seen.add(key)
        spans.append(span)
        return len(spans) < limit

    for line in source.splitlines():
        if _PLANNING_LINE.match(line):
            continue
        found: list = []
        for match in _SUPERLATIVE_SPAN.finditer(line):
            found.append((match.start(), _clean(match.group(0))))
        for match in _ANCHOR.finditer(line):
            # The unit group may have eaten the space after the number, which
            # would leave the noun unreachable: "40,000 " then "naira".
            anchor = match.group(0)
            tail = line[match.end() - (len(anchor) - len(anchor.rstrip())):]
            anchor = anchor.rstrip()
            # Up to three following words, stopping at any punctuation that ends
            # the noun phrase. "seven men, seated" gives "seven men".
            noun = _following_noun(tail)
            found.append((match.start(), _clean(f"{anchor} {noun}" if noun else anchor)))
        for _, span in sorted(found, key=lambda pair: pair[0]):
            if not consider(span):
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


# How near the superlative the qualifier has to sit to still be qualifying it.
# "one of the country's deadliest" is 24 characters; a qualifier further off
# than this is attached to something else.
_SET_WINDOW = 40

_SET_MARKER = re.compile(r"\b(?:one|two|three|four|five|several|some|a few|among)\s+(?:of\s+)?the?\b")


def _dropped_the_set_it_belongs_to(needle: str, haystack: str) -> bool:
    """Whether a draft states a "one of the ..." claim as an outright "the ...".

    Narrower than the rule for numbers, and deliberately. "More than seven men"
    has no second correct wording, so any restatement is a loss. A superlative
    has many: "one of the country's deadliest" says exactly what "one of the
    deadliest in the country" says, and the brand's own second draft wrote it
    that way. Faulting that would be the tight-rule failure again.

    What has no second correct wording is the set. Being one of the deadliest
    and being the deadliest are different claims, and collapsing them is the
    same move as writing "seven men" for "more than seven men" — which is how a
    draft came to call the group "the deadliest in the state" when the source
    called it the most feared in the state and one of the deadliest in the
    country.

    So: only claims whose source form is a set membership are checked, and only
    for whether the draft kept a set anywhere near where it makes the claim.
    """
    if not _SET_MARKER.search(needle):
        return False
    match = _SUPERLATIVE_WORD.search(needle)
    word = (match.group(1) or match.group(2)) if match else ""
    if not word:
        return False
    for found in re.finditer(rf"\b{re.escape(word)}\b", haystack):
        before = haystack[max(0, found.start() - _SET_WINDOW):found.start()]
        if not _SET_MARKER.search(before):
            return True          # stated, and stated as the only one
    return False


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
        anchor = next((w for w in needle.split()
                       if re.search(r"\d", w) or w in _NUMBER_WORD_SET), "")
        if anchor:
            if re.search(rf"\b{re.escape(anchor)}\b", haystack):
                missing.append(span)
            continue
        if _dropped_the_set_it_belongs_to(needle, haystack):
            missing.append(span)
    return missing
