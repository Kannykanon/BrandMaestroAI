"""Turn an approved script into spoken lines and shots, without changing a word.

This is deliberately deterministic code, not a model call. The approved text
never passes through an LLM: lines are classified by their shape, speakers come
from their labels, and a shot's text is always a slice of the script. The
scene planner (youtube/planner.py) only annotates shots afterwards.

Recognised shapes:
    MAYA: We did it.                 dialogue, speaker MAYA
    Maya: We did it.                 dialogue (title-case name, one or two words)
    MAYA (whispering): We did it.    dialogue, delivery note "whispering"
    MAYA                             speaker cue; following lines until a blank
    We did it.                       line are MAYA's dialogue
    VO: / NARRATOR: / Voiceover:     narration
    HOOK: / INTRO: / CTA:            narration; the label names a section
    Plain paragraph                  narration
    [Cut to the office] / (beat)     direction: shown, never spoken
    INT. OFFICE - NIGHT              direction (scene heading)
    VISUAL: / ON SCREEN: / SFX:      direction
    # Scene 1 / **Scene 1** / ---    direction

Inline (parentheticals) and [brackets] inside a spoken line are delivery notes
and are not spoken. Markdown emphasis markers are not spoken.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

NARRATOR = "NARRATOR"

SPOKEN = "spoken"
DIRECTION = "direction"

NARRATOR_LABELS = {"VO", "V.O", "V.O.", "VOICEOVER", "VOICE-OVER", "VOICE OVER", "NARRATOR", "NARRATION", "NARR"}
SECTION_LABELS = {
    "HOOK", "INTRO", "INTRODUCTION", "OPENING", "BODY", "MAIN", "CLOSE", "CLOSING",
    "OUTRO", "CTA", "CALL TO ACTION", "CONCLUSION", "SUMMARY", "TEASER",
    # Ad copy
    "HEADLINE", "SUBHEADLINE", "SUB-HEADLINE", "SUBHEAD", "TAGLINE", "SLOGAN", "BODY COPY", "COPY",
    "AD COPY", "PRIMARY TEXT", "DESCRIPTION", "OFFER",
}
DIRECTION_LABELS = {
    "VISUAL", "VISUALS", "ON SCREEN", "ON-SCREEN", "ONSCREEN", "SCENE", "SHOT", "CAMERA",
    "B-ROLL", "BROLL", "B ROLL", "SFX", "SOUND", "MUSIC", "TEXT", "SUPER", "GRAPHIC",
    "GRAPHICS", "TITLE", "LOWER THIRD", "TRANSITION", "CUT TO", "FADE IN", "FADE OUT",
    "SETTING", "LOCATION", "NOTE", "NOTES", "DIRECTION", "STAGE DIRECTION",
}

_EMPHASIS = re.compile(r"(\*\*|__|\*|~~)")
_BULLET = re.compile(r"^\s*(?:[-*•]\s+)")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_SCENE_HEADING = re.compile(r"^\s*(INT|EXT|INT\./EXT|INT/EXT|I/E|EST)[.\s]", re.IGNORECASE)
_TRANSITION = re.compile(r"^\s*(CUT TO|FADE IN|FADE OUT|FADE TO BLACK|DISSOLVE TO|SMASH CUT TO)\s*[:.]?\s*$", re.IGNORECASE)
_WHOLLY_ENCLOSED = re.compile(r"^\s*(\[[^\]]*\]|\([^)]*\))\s*$")
_BOLD_ONLY = re.compile(r"^\s*(\*\*|__)[^*_]+(\*\*|__)\s*$")
_INLINE_NOTE = re.compile(r"\s*(\([^)]*\)|\[[^\]]*\])\s*")
# LABEL (note): text   — label may be wrapped in ** or __.
_LABEL_LINE = re.compile(
    r"^\s*(?:\*\*|__)?(?P<label>[A-Za-z][A-Za-z0-9.'&\- ]{0,40}?)"
    r"(?:\s*\((?P<note>[^)]*)\))?(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(?P<text>.*)$"
)
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+")


@dataclass
class Segment:
    """One line of the script, classified. `source` is the exact original line."""
    index: int
    kind: str          # spoken | direction
    speaker: str       # canonical speaker for spoken lines, "" for directions
    source: str
    spoken: str = ""   # what is read aloud
    delivery: str = "" # delivery notes, e.g. "whispering"
    section: str = ""  # e.g. "HOOK"


@dataclass
class Shot:
    position: int
    speaker: str
    text: str
    segment_indexes: list[int] = field(default_factory=list)
    delivery: str = ""
    context: str = ""  # nearby directions, for the planner


class ScriptError(ValueError):
    """The script cannot be turned into shots without changing it."""


def strip_emphasis(text: str) -> str:
    return _EMPHASIS.sub("", text)


def words(text: str) -> list[str]:
    """Words for comparison: markdown markers and spacing do not count, everything else does."""
    return strip_emphasis(text).replace("#", " ").split()


def canonical_speaker(label: str) -> str:
    upper = re.sub(r"\s+", " ", label.strip().upper())
    if upper in NARRATOR_LABELS or upper.rstrip(".") in NARRATOR_LABELS:
        return NARRATOR
    return upper


def _label_kind(label: str) -> str | None:
    """How a `LABEL:` prefix should be read, or None if it is not a label at all."""
    clean = re.sub(r"\s+", " ", label.strip())
    upper = clean.upper()
    if upper in DIRECTION_LABELS:
        return "direction"
    if upper in SECTION_LABELS:
        return "section"
    if upper in NARRATOR_LABELS or upper.rstrip(".") in NARRATOR_LABELS:
        return "narrator"
    words_in_label = clean.split()
    if not 1 <= len(words_in_label) <= 4:
        return None
    letters = [c for c in clean if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return "speaker"
    # Title-case names ("Maya:", "Dr Chen:") — at most two words, letters only,
    # so ordinary prose with a colon ("Here's the truth: ...") is not a label.
    if len(words_in_label) <= 2 and all(w[:1].isupper() and w.replace(".", "").replace("'", "").isalpha()
                                       for w in words_in_label):
        return "speaker"
    return None


def _is_speaker_cue(line: str) -> bool:
    """A screenplay character cue on its own line: MAYA, or MAYA (V.O.)."""
    stripped = strip_emphasis(line).strip()
    match = re.match(r"^([A-Z][A-Z0-9.'&\- ]{0,30}?)(\s*\([^)]*\))?$", stripped)
    if not match:
        return False
    name = match.group(1).strip()
    if name.upper() in DIRECTION_LABELS or _SCENE_HEADING.match(stripped) or _TRANSITION.match(stripped):
        return False
    return 1 <= len(name.split()) <= 3 and not name.endswith(".")


def _clean_spoken(text: str) -> tuple[str, str]:
    """Remove delivery notes and markup from a spoken line. Returns (spoken, delivery)."""
    notes = [m.group(1)[1:-1].strip() for m in _INLINE_NOTE.finditer(text)]
    spoken = _INLINE_NOTE.sub(" ", text)
    spoken = _BULLET.sub("", spoken)
    spoken = re.sub(r"\s+", " ", strip_emphasis(spoken)).strip()
    return spoken, "; ".join(n for n in notes if n)


def parse_script(script: str) -> list[Segment]:
    """Classify every non-blank line. Joining the segments' sources gives back the script's words."""
    segments: list[Segment] = []
    cue_speaker = ""   # set by a speaker cue or an empty `LABEL:` line; ends at a blank line
    cue_delivery = ""

    def add(kind, speaker, source, spoken="", delivery="", section=""):
        segments.append(Segment(len(segments), kind, speaker, source, spoken, delivery, section))

    for raw in script.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            cue_speaker, cue_delivery = "", ""
            continue

        if (_HEADING.match(line) or _RULE.match(line) or _SCENE_HEADING.match(line)
                or _TRANSITION.match(line) or _WHOLLY_ENCLOSED.match(strip_emphasis(line))
                or _BOLD_ONLY.match(line)):
            add(DIRECTION, "", line)
            continue

        match = _LABEL_LINE.match(line)
        kind = _label_kind(match.group("label")) if match else None
        if match and kind:
            label, note, text = match.group("label"), (match.group("note") or "").strip(), match.group("text")
            if kind == "direction":
                add(DIRECTION, "", line)
                continue
            speaker = NARRATOR if kind in ("narrator", "section") else canonical_speaker(label)
            section = label.strip().upper() if kind == "section" else ""
            spoken, inline_notes = _clean_spoken(text)
            delivery = "; ".join(n for n in (note, inline_notes) if n)
            if spoken:
                add(SPOKEN, speaker, line, spoken, delivery, section)
                cue_speaker, cue_delivery = "", ""
            else:
                # "MAYA:" alone — the following lines are hers.
                add(DIRECTION, "", line)
                cue_speaker, cue_delivery = speaker, delivery
            continue

        if not cue_speaker and _is_speaker_cue(line):
            cue_match = re.match(r"^\s*(.*?)\s*(?:\(([^)]*)\))?\s*$", strip_emphasis(line))
            cue_speaker = canonical_speaker(cue_match.group(1))
            cue_delivery = (cue_match.group(2) or "").strip()
            add(DIRECTION, "", line)
            continue

        spoken, delivery = _clean_spoken(line)
        if not spoken:
            add(DIRECTION, "", line)
            continue
        speaker = cue_speaker or NARRATOR
        add(SPOKEN, speaker, line, spoken, "; ".join(n for n in (cue_delivery if cue_speaker else "", delivery) if n))

    return segments


def _split_narration(text: str, max_words: int) -> list[str]:
    """Split narration at sentence ends into chunks of at most max_words (a long sentence stays whole)."""
    sentences = [s for s in _SENTENCE_END.split(text) if s.strip()]
    # _SENTENCE_END consumes closing quotes with the whitespace; put them back.
    rebuilt, cursor = [], 0
    for sentence in sentences:
        start = text.index(sentence, cursor)
        end = start + len(sentence)
        while end < len(text) and text[end] in "\"'”’)]":
            end += 1
        rebuilt.append(text[start:end].strip())
        cursor = end

    chunks, current = [], []
    for sentence in rebuilt:
        if current and len(" ".join(current + [sentence]).split()) > max_words:
            chunks.append(" ".join(current))
            current = []
        current.append(sentence)
    if current:
        chunks.append(" ".join(current))
    return chunks


def build_shots(segments: list[Segment], max_narration_words: int = 25) -> list[Shot]:
    """One shot per dialogue line; narration split at sentence ends into shots of about 25 words.

    About 25 words is 8-10 seconds of speech: how long a narration image stays on screen.
    """
    shots: list[Shot] = []
    pending_context: list[str] = []

    for segment in segments:
        if segment.kind == DIRECTION:
            pending_context.append(strip_emphasis(segment.source).strip())
            continue
        context = " | ".join(pending_context)
        previous = shots[-1] if shots else None
        # Consecutive lines from one character, with nothing between them, are
        # one continuous speech and so one shot.
        if (previous and segment.speaker != NARRATOR and previous.speaker == segment.speaker
                and not pending_context and previous.segment_indexes[-1] == segment.index - 1):
            previous.text = f"{previous.text} {segment.spoken}"
            previous.segment_indexes.append(segment.index)
            if segment.delivery:
                previous.delivery = "; ".join(d for d in (previous.delivery, segment.delivery) if d)
            continue
        pending_context = []
        pieces = (_split_narration(segment.spoken, max_narration_words)
                  if segment.speaker == NARRATOR else [segment.spoken])
        for i, piece in enumerate(pieces):
            shots.append(Shot(
                position=len(shots) + 1,
                speaker=segment.speaker,
                text=piece,
                segment_indexes=[segment.index],
                delivery=segment.delivery,
                context=context if i == 0 else "",
            ))
    return shots


def verify_words(script: str, segments: list[Segment], shots: list[Shot]) -> None:
    """Prove no approved word was added, dropped, reordered or reworded. Raises ScriptError.

    1. The segments cover the script exactly.
    2. Every spoken line's words appear, in order, in its source line (only
       labels, delivery notes and markup were removed).
    3. The shots read exactly the spoken words, in order.
    """
    if words("\n".join(s.source for s in segments)) != words(script):
        raise ScriptError("Parsed lines do not cover the script exactly")

    for segment in segments:
        if segment.kind != SPOKEN:
            continue
        source_words = iter(words(segment.source))
        if not all(any(w == s for s in source_words) for w in words(segment.spoken)):
            raise ScriptError(f"Line {segment.index + 1} would be read differently from the script")

    spoken_words = [w for s in segments if s.kind == SPOKEN for w in words(s.spoken)]
    shot_words = [w for shot in shots for w in words(shot.text)]
    if shot_words != spoken_words:
        raise ScriptError("Shots do not read exactly the script's spoken words")


def plan_shots(script: str, max_narration_words: int = 25) -> tuple[list[Segment], list[Shot]]:
    """Parse, split into shots and verify. The only entry point the pipeline uses."""
    if not script.strip():
        raise ScriptError("The script is empty")
    segments = parse_script(script)
    shots = build_shots(segments, max_narration_words)
    if not shots:
        raise ScriptError("The script has no lines to read aloud")
    verify_words(script, segments, shots)
    return segments, shots
