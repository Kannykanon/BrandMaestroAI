"""What the draft dropped, and where it stopped showing and started summarising.

Two failures that every other check in this system is blind to, both visible in
the same generated act:

    This encounter marks his initiation into a world of hidden power, a place
    demanding loyalty and severe consequences. His choices now determine a path
    from which there is absolutely no return.

Nothing there is false, copied, unattributed or off-brand in any way a rate can
measure. It is simply not the story. The source for that stretch has EMK's
manner shifting, him asking about Kan's studies, other men greeting him with
both hands, a phone number handed over with "call me if you ever run into
trouble" — and the draft replaced all of it with a description of what it all
means. It told the reader how to feel instead of showing what happened, and
lost real material doing it.

Two measurements catch that:

`dropped_detail` compares the draft against the source beat by beat, and
reports the beats the draft reaches and then empties out. Reaching a beat
matters: a piece that never covers a scene has made an editorial choice, while
one that covers it without its detail has thrown the detail away.

`vague_sections` measures abstraction per section rather than over the whole
draft. The act above sits inside a draft whose overall register is fine; the
average hid it. A piece that collapses in its last section is the common shape
of this failure — the source material runs out and the writing turns to
summary — and averages are exactly the wrong instrument for it.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from utils.voice_spec import _is_heading, sentences, shape_metrics

_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")

# Grammar and connective tissue. A beat is not "covered" because the draft also
# contains the word "the".
_STOPWORDS = frozenset("""
a an the and or but so then than that this these those there here it its it's
is are was were be been being am do does did done have has had having will
would can could may might must shall should of in on at by for with from to
into onto up down out off over under again further once about against between
through during before after above below as until while because if not no nor
only own same too very just now new more most other some any each few own
he him his she her hers they them their theirs we us our you your i me my
who whom which what when where why how all both such
one two three four five six seven eight nine ten
says said say goes go going get gets got come comes came take takes took
""".split())

# The source's own abstractions are not detail to be preserved. A treatment
# saying a moment "marks the beginning of his entanglement" is describing its
# own story; the draft's job is the thing described, not the description.
_ABSTRACT = re.compile(r"(tion|sion|ment|ance|ence|ity|ness|ship|ism|hood)s?$", re.IGNORECASE)

# Words that name what something means rather than what happened. A treatment
# writes "a title, and a role, that carries real weight in that world", and
# "Status. Deference. Fear, maybe." The right retelling does not reproduce any
# of that — it shows men greeting EMK with both hands and lets the reader do
# the rest, which is precisely what the hand-written one does. Demanding these
# words back would be demanding the draft explain itself.
_MEANING = frozenset("""
weight role title world status deference fear sense idea thing things something
way ways matter matters power loyalty meaning point level nature kind sort
beginning end future past reality truth
mark marks marked regard regarded widely moment eventually entanglement
""".split())

# Sections of a source document that are planning, not story.
_NOT_STORY = frozenset({
    "profiles", "profile", "notes", "note", "continuation", "format", "genre",
    "characters", "character", "cast", "logline", "treatment", "deliverables",
    "themes", "tone", "audience", "objectives", "objective", "assumptions",
    "scope", "overview", "structure", "background",
})

# How much of a beat's concrete detail must survive for the draft to be
# carrying it rather than summarising it. Set from the two Kancity drafts: the
# hand-written retelling keeps most of every beat it covers, and the generated
# act that replaced four beats with two sentences of thriller copy keeps almost
# none. Anything in between is ambiguous and deliberately not reported.
KEPT_ENOUGH = 0.34

# Below this a beat is too thin to judge — one clause with three content words
# says nothing about whether the draft engaged with it.
MIN_BEAT_WORDS = 6

# Below this share of a beat's own words, the draft is not telling that beat at
# all. Skipping one in the middle of the story is still reported: the draft
# that prompted this jumped from the drink to "his life changed irrevocably",
# losing the two-handed greetings, the questions about his studies and the
# phone number on the way. Only beats after the last one the draft engages with
# are exempt, which is how a piece that covers the opening and stops stays a
# legitimate choice of scope.
REACHED = 0.12


def _normalise(word: str) -> str:
    """Crude stemming. Enough to match "greets" to "greet" and "asking" to "ask"."""
    word = re.sub(r"['’]s$", "", word.lower()).strip("'’-")
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _meaning_stems() -> frozenset:
    """_MEANING as the stemmer will actually see it.

    Words are normalised before this set is consulted, so "beginning" was
    compared as "beginn" and never matched the entry written out in full — the
    brief's framing went on being demanded from every retelling.
    """
    return frozenset(_normalise(word) for word in _MEANING)


_MEANING_STEMS = _meaning_stems()


def content_words(text: str) -> set:
    """The words that carry what happened: concrete, not grammar, not abstraction."""
    words = set()
    for raw in _WORD.findall(text):
        if len(raw) < 3 or raw.lower() in _STOPWORDS or _ABSTRACT.search(raw):
            continue
        word = _normalise(raw)
        if word in _MEANING_STEMS:
            continue
        words.add(word)
    return words


# A document talking about itself: its format, its status, how many parts are
# planned. Caught by content rather than by position, because the heading it
# sits under does not always survive — retrieval returns chunks, and a fallback
# to the research blob gets prose with its structure gone.
_ABOUT_THE_DOCUMENT = re.compile(
    r"\b(?:status:|part \d+ of|installments?|treatment|screenplay|logline|"
    r"based on true events|to be added|ongoing account|draft \d|version \d)\b",
    re.IGNORECASE,
)


def _is_about_the_document(beat: str) -> bool:
    """Whether a beat describes the document rather than anything in the story.

    "Film / Limited Series Treatment — Based on True Events. Status: Part 1 of
    an ongoing account. Additional installments to be added as the story
    continues." is a title block. It was reported as story a draft had failed to
    tell, in every round of a run, and no draft could ever have satisfied it.
    """
    return bool(_ABOUT_THE_DOCUMENT.search(beat))


def story_beats(source: str) -> list[str]:
    """The source's story paragraphs, with its planning sections left out.

    The pipeline's own banners go first. They explain to the writer how to treat
    each source, and a note about which source wins a disagreement was read as a
    beat to dramatise — for six rounds, a draft was told it had failed to tell
    it. See utils/research_sections.py.
    """
    from utils.research_sections import strip_framing

    source = strip_framing(source)
    # Only headings at the document's own section level switch sections. The
    # character list is introduced by "## CHARACTER PROFILES" and each entry by
    # a bold name, and treating "**EMK**" as a new section turned the profiles
    # back on — so the writer was told it had dropped detail from a cast note.
    sectioning = "#" if re.search(r"^#{1,6}\s", source, re.M) else ""
    beats, current, skipping = [], [], False
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                beats.append(" ".join(current))
                current = []
            continue
        if _is_heading(stripped):
            is_section = stripped.startswith(sectioning) if sectioning else True
            if current:
                beats.append(" ".join(current))
                current = []
            if is_section:
                words = {w.lower() for w in _WORD.findall(stripped)}
                skipping = bool(words & _NOT_STORY)
            continue
        if not skipping:
            current.append(stripped)
    if current:
        beats.append(" ".join(current))
    return [b for b in beats
            if len(_WORD.findall(b)) >= MIN_BEAT_WORDS and not _is_about_the_document(b)]


def dropped_detail(content: str, source: str, abstraction_high: float = 0.0,
                   limit: int = 4) -> list[dict]:
    """Beats the draft reaches and then summarises instead of telling.

    Each finding names the beat and the concrete words that vanished, so the
    feedback can say what to put back rather than "add more detail".
    """
    if not content or not source:
        return []
    draft_words = content_words(content)
    if not draft_words:
        return []

    beats = story_beats(source)

    # Only words that belong to this beat in particular are asked for. Words the
    # source uses throughout — "Kan", "number", "call" — say nothing about
    # whether the draft told *this* part: a phone call in act one otherwise
    # counts as covering the phone number EMK hands over in act three, which is
    # exactly the beat that went missing while the check said it was there.
    appearances: dict = {}
    for beat in beats:
        for word in content_words(beat):
            appearances[word] = appearances.get(word, 0) + 1
    common = max(2, len(beats) // 4)

    scored = []
    for beat in beats:
        wanted = {w for w in content_words(beat) if appearances[w] <= common}
        if len(wanted) < 4:
            scored.append((beat, None, set()))
            continue
        kept = wanted & draft_words
        scored.append((beat, len(kept) / len(wanted), wanted - draft_words))

    # Where the draft stops telling, and whether it stopped writing too.
    #
    # A piece that covers the opening and ends there has chosen its scope, and
    # demanding the rest of the treatment from a trailer would be absurd. A
    # piece that covers the opening and then keeps going — pages of it, telling
    # none of what remains — has not chosen anything; it has run out and started
    # summarising. The measured difference is real: a trailer's words are all
    # accounted for by the beats it tells, while the act that prompted this
    # wrote "his initiation into a world of hidden power" over four beats it
    # never told, and that section reads as abstraction against its own draft.
    told = [i for i, (_, ratio, _) in enumerate(scored) if ratio is not None and ratio >= KEPT_ENOUGH]
    if not told:
        return []
    last_told = told[-1]
    still_writing = bool(abstraction_high) and bool(vague_sections(content, abstraction_high))

    findings = []
    for index, (beat, ratio, missing) in enumerate(scored):
        if ratio is None or ratio >= KEPT_ENOUGH:
            continue
        # Past the last beat it tells, a draft is only answerable for what it
        # kept writing instead.
        if index > last_told and not still_writing:
            continue
        findings.append({
            "beat": beat[:220],
            "missing": sorted(missing)[:12],
            "kept_ratio": round(ratio, 2),
            # Told apart because the fix differs: a thinned beat needs its
            # detail put back, a skipped one needs writing at all.
            "skipped": ratio < REACHED,
        })
    findings.sort(key=lambda f: f["kept_ratio"])
    return findings[:limit]


def _sections(text: str) -> list[tuple[str, str]]:
    """The draft split at its own headings, as (title, body) pairs."""
    from utils.voice_spec import body_lines

    sections, title, body = [], "", []
    for line in body_lines(text):
        stripped = line.strip()
        if set(stripped) <= set("=-_~*# "):
            continue  # a rule between sections is not a section title
        if _is_heading(stripped):
            if body:
                sections.append((title, "\n".join(body)))
                body = []
            title = stripped
            continue
        if stripped:
            body.append(stripped)
    if body:
        sections.append((title, "\n".join(body)))
    return sections


# How far past the brand's own range a section has to run before it is worth
# mentioning. Wide, because this is advisory: it feeds the voice pass as
# evidence rather than refusing anything, and a section that merely sits at the
# top of the range is a section written normally.
VAGUE_MULTIPLE = 1.5


def vague_sections(content: str, high: float, min_words: int = 25) -> list[dict]:
    """Sections that have stopped telling and started summarising.

    Measured per section because the failure is local. The draft that prompted
    this was within range overall and had turned entirely to summary by its last
    act — "This encounter marks his initiation into a world of hidden power" —
    and the average across the whole piece hid it completely.

    Advisory, never blocking. Whether naming a thing instead of showing it is
    wrong depends on what is being written: it is the defect in a scene and the
    job in a proposal, and no rate can tell those apart. So this reports the
    sections and the sentences doing it, and the voice pass decides.
    """
    if not content or not high:
        return []

    # Measured twice over: against the brand's own range, and against the rest
    # of this draft. The second comparison is the one that finds this failure.
    # The act that prompted it runs at 4.6 abstractions per 100 words against
    # 1.5 and 1.2 in the two acts before it — a piece that starts concrete and
    # gives up — and no fixed multiple of the brand's range separates that from
    # a brand that simply writes a little abstractly throughout.
    measured = []
    for title, body in _sections(content):
        metrics = shape_metrics(body)
        if metrics.get("words", 0) >= min_words:
            measured.append((title, body, metrics.get("nominalisations_per_100_words", 0.0)))
    if not measured:
        return []
    rates = sorted(rate for _, _, rate in measured)
    if len(rates) > 1:
        typical = rates[len(rates) // 2] if len(rates) > 2 else min(rates)
        floor = max(typical * 2, 1.0)
    else:
        # One section long enough to measure, so there is nothing to compare it
        # against but the brand. Judged on that alone it has to be further out
        # before it is worth saying anything — a single-section piece that is
        # simply abstract is a choice, not a collapse.
        floor = high * VAGUE_MULTIPLE

    findings = []
    for title, body, rate in measured:
        if rate <= high or rate < floor:
            continue
        findings.append({
            "section": title or "the closing section",
            "rate": rate,
            "high": high,
            "examples": _abstract_examples(body),
        })
    return findings


def _abstract_examples(text: str) -> list[str]:
    """The sentences doing the summarising, so feedback can point at them."""
    scored = []
    for sentence in sentences(text):
        words = _WORD.findall(sentence)
        if len(words) < 5:
            continue
        abstractions = sum(1 for w in words if _ABSTRACT.search(w))
        if abstractions:
            scored.append((abstractions / len(words), sentence.strip()))
    scored.sort(reverse=True)
    return [s for _, s in scored[:2]]

# Verbs a narrator uses to tell the reader what something meant.
_EXPLAINS = (
    "shows|show|showed|signals|signal|signalled|signaled|signifies|signify|signified|"
    "indicates|indicate|indicated|means|mean|meant|represents|represent|represented|"
    "symbolises|symbolizes|symbolise|symbolize|conveys|convey|conveyed|implies|imply|"
    "implied|suggests|suggest|suggested|carries|carry|carried|marks|mark|marked|"
    "demonstrates|demonstrate|demonstrated|reflects|reflect|reflected"
)

# Who is doing the explaining. A character noticing something is the scene; a
# gesture explaining itself is the narrator leaning in.
#
# The line between them is the subject, and this brand's own hand-written script
# draws it cleanly. It writes "Kan notices. He does not fully understand what it
# means. But he understands that it means something." — animate, uncertain, the
# character's own experience. The draft it was compared against wrote "The
# gesture shows deference. It shows fear." Same information, told rather than
# played, and nothing in the system could see the difference.
_INANIMATE_SUBJECT = (
    r"(?:it|this|that|these|those|the\s+\w+(?:\s+\w+)?|his\s+\w+|her\s+\w+|their\s+\w+|"
    r"a\s+\w+|an\s+\w+)"
)

_NARRATOR_EXPLAINS = re.compile(
    rf"\b{_INANIMATE_SUBJECT}\s+(?:{_EXPLAINS})\b",
    re.IGNORECASE,
)

# Subjects that are people, however the sentence starts. "He shows deference" is
# somebody doing something.
_PERSON_PRONOUNS = frozenset("he she they we i you him her them".split())

# Words that begin a sentence in capitals without being anybody's name. Left in,
# "The" matched the proper-noun branch, so every sentence opening with a
# determiner read as a person doing something — which is most of them, and the
# check found nothing at all.
_NOT_A_NAME = frozenset("""
the this that these those it its his hers their there here a an one other others
some any each every no before after when while as and but so then now
""".split())


def _mentions_person(text: str) -> bool:
    """Whether anybody is present in this stretch of a sentence.

    Read only from the first word, this missed the clause that matters. The
    hand-written script writes "But he understands that it means something" —
    "But" is not a name, "it means" is the pattern, and the sentence was
    reported as the narrator explaining when it is the character failing to.
    Anybody named before the verb makes the explanation theirs.
    """
    for word in re.findall(r"[A-Za-z']+", text):
        if word.lower() in _PERSON_PRONOUNS:
            return True
        if word[:1].isupper() and word.lower() not in _NOT_A_NAME:
            return True
    return False


def narrator_explanations(content: str, limit: int = 5) -> list:
    """Sentences where the narrator states what something meant.

    Reported, not blocked. Whether an explanation is wrong depends on what is
    being written — a proposal explains for a living — so this says what it
    found and the piece's reviewer decides.
    """
    if not content:
        return []
    found = []
    for sentence in sentences(prose_only_for_scan(content)):
        stripped = sentence.strip()
        match = _NARRATOR_EXPLAINS.search(stripped)
        if not match:
            continue
        if _mentions_person(stripped[:match.start()]):
            continue  # somebody's own understanding, not the narrator's aside
        # Only when what follows is a meaning rather than a thing: "the gesture
        # shows deference", not "the door shows a scratch".
        tail = stripped[match.end():]
        words = {_normalise(w) for w in _WORD.findall(tail)[:6]}
        if not (words & _MEANING_STEMS) and not any(_ABSTRACT.search(w) for w in _WORD.findall(tail)[:6]):
            continue
        found.append({
            "sentence": stripped[:160],
            "message": f"states what something meant instead of showing it: {stripped[:90]!r}",
        })
        if len(found) >= limit:
            break
    return found


def prose_only_for_scan(text: str) -> str:
    """The draft's prose, tolerant of fragments that prose_only() would strip."""
    from utils.voice_spec import _is_heading

    return "\n".join(line.strip() for line in text.splitlines()
                      if line.strip() and not _is_heading(line))
