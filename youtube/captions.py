"""Captions: when each word is spoken, grouped into short on-screen cues, written as ASS subtitles.

Kokoro gives no reliable word timings, so timings are estimated inside each
shot: the shot's own audio clip is split between its words in proportion to
their length, with extra weight after punctuation where speech pauses. Each
shot is timed separately, so an error never drifts past the end of its line.

    YT_CAPTION_FONT       font family (default "DejaVu Sans", installed in the render image)
    YT_CAPTION_MAX_WORDS  most words on screen at once (default 5)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

PAUSE_WEIGHT = {",": 2.0, ";": 2.5, ":": 2.5, ".": 4.0, "!": 4.0, "?": 4.0, "…": 4.0}
# Clips are padded slightly by TTS; words never start in the first or last sliver.
EDGE_PADDING_S = 0.05


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Cue:
    text: str
    start: float
    end: float


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def word_timings(text: str, start: float, duration: float) -> list[TimedWord]:
    """Estimate when each word of a line is spoken within [start, start + duration]."""
    words = text.split()
    if not words or duration <= 0:
        return []
    usable_start = start + min(EDGE_PADDING_S, duration / 10)
    usable = max(duration - 2 * min(EDGE_PADDING_S, duration / 10), 0.01)

    weights = []
    for word in words:
        letters = len(re.sub(r"[^\w]", "", word)) or 1
        pause = PAUSE_WEIGHT.get(word[-1], 0.0)
        weights.append((letters + 1.0, pause))  # +1 for the gap between words
    total = sum(w + p for w, p in weights)

    timed, cursor = [], usable_start
    for word, (weight, pause) in zip(words, weights):
        length = usable * weight / total
        timed.append(TimedWord(word, cursor, cursor + length))
        cursor += length + usable * pause / total
    return timed


def build_cues(words: list[TimedWord], max_words: int | None = None, max_chars: int = 32) -> list[Cue]:
    """Group words into short cues, breaking at sentence ends and clause punctuation."""
    max_words = max_words or _env_int("YT_CAPTION_MAX_WORDS", 5)
    cues, current = [], []

    def flush():
        if current:
            cues.append(Cue(" ".join(w.text for w in current), current[0].start, current[-1].end))
            current.clear()

    for word in words:
        if current and (len(current) >= max_words or len(" ".join(w.text for w in current + [word])) > max_chars):
            flush()
        current.append(word)
        if word.text[-1] in ".!?…":
            flush()
        elif word.text[-1] in ",;:" and len(current) >= 3:
            flush()
    flush()
    return cues


def _ass_time(seconds: float) -> str:
    centis = max(int(round(seconds * 100)), 0)
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_text(text: str) -> str:
    # Braces start override tags and a backslash starts escapes in ASS.
    return text.replace("\\", "⧵").replace("{", "(").replace("}", ")").replace("\n", " ")


def to_ass(cues: list[Cue], width: int, height: int, font: str | None = None) -> str:
    """An ASS subtitle file: large white text with a dark outline, centred low in the frame."""
    font = font or os.getenv("YT_CAPTION_FONT", "").strip() or "DejaVu Sans"
    vertical = height > width
    size = round(height * (0.042 if vertical else 0.055))
    margin_v = round(height * (0.22 if vertical else 0.08))  # clear of Shorts' own interface
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(2, size // 14)},1,2,{round(width * 0.08)},{round(width * 0.08)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Caption,,0,0,0,,{_ass_text(c.text)}" for c in cues]
    return header + "\n".join(lines) + ("\n" if lines else "")
