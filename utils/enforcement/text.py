"""Small text utilities shared by the enforcement checks."""
import re


_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def tokens_with_offsets(text: str):
    return [(m.group(0).lower(), m.start(), m.end()) for m in _WORD_RE.finditer(text)]


def quoted_regions(text: str):
    """Character ranges inside quotation marks.

    A press release legitimately quotes its source verbatim, and inventing a
    quote is already caught by find_unverified_quote_attributions, so quoted
    spans are exempt from the extractive check rather than double-penalised.
    """
    return [
        (m.start(), m.end())
        for m in re.finditer(r'"[^"]{0,800}"|“[^”]{0,800}”', text)
    ]


# Script-format dialogue: a speaker in capitals, an optional parenthetical
# direction, a colon, then the line. Matches "WALE: Sixteen days." and
# "SAM (over comms, breathing hard): Wale, she's not coming up."
#
# The speaker part allows no lowercase, so section headings that happen to
# contain a colon ("Duration 0:94") and label lines that use a dash rather than
# a colon ("INSTAGRAM — teaser drop") are not mistaken for dialogue.
_SCRIPT_LINE_RE = re.compile(
    r"^[A-Z][A-Z0-9 .'\-]{0,30}(?:\([^)\n]{0,60}\))?:[ \t]*(\S.*)$",
    re.MULTILINE,
)


def script_dialogue_lines(text: str):
    """(speaker, line, start, end) for each script-format dialogue line."""
    out = []
    for m in _SCRIPT_LINE_RE.finditer(text):
        speaker = m.group(0)[: m.group(0).index(":")].strip()
        # Drop any parenthetical direction from the speaker label.
        speaker = re.sub(r"\s*\([^)]*\)\s*$", "", speaker).strip()
        out.append((speaker, m.group(1), m.start(1), m.end(1)))
    return out


_CAPS_WORD_RE = re.compile(r"(?<![A-Za-z])[A-Z][A-Z0-9'&.-]{2,}(?![A-Za-z])")


def inline_caps_words(text: str):
    """All-caps words appearing inside a line that also contains lowercase.

    A word in capitals on a line of its own is structural — a card, a heading, a
    label — and the brand's own conventions govern it. A word in capitals in the
    middle of a sentence is emphasis, and whether the brand does that is a
    separate question from how many capitals it uses overall.
    """
    out = []
    for line_match in re.finditer(r"^.*$", text, re.MULTILINE):
        line = line_match.group(0)
        if not re.search(r"[a-z]", line):
            continue          # caps-only line: structural, not emphasis
        for m in _CAPS_WORD_RE.finditer(line):
            out.append((m.group(0), line_match.start() + m.start()))
    return out


def script_dialogue_regions(text: str):
    """Character ranges of the spoken part of script-format dialogue lines.

    A line of dialogue on a trailer copy sheet is a quotation of the film. It
    cannot be paraphrased — an actor said those words — so it belongs with
    quoted text: exempt from the extractive-copying check, and protected from
    alteration by the quotation-fidelity check.
    """
    return [(m.start(1), m.end(1)) for m in _SCRIPT_LINE_RE.finditer(text)]


# A line whose letters are all capitals, with at least one word of three or
# more letters. On a trailer copy sheet this is card copy — the text that goes
# on screen.
_CARD_LINE_RE = re.compile(
    r"^[^a-z\n]*[A-Z]{3,}[^a-z\n]*$",
    re.MULTILINE,
)


def card_line_regions(text: str):
    """Character ranges of all-capitals card lines.

    Card copy is a fixed asset, like a company's boilerplate: "THE HOLD IS NOT
    EMPTY" is the card, and a trailer sheet that paraphrases it is describing a
    different trailer. Exempt from the extractive-copying check for the same
    reason boilerplate is.

    This does not license a draft to be nothing but capitals — the measured
    all-caps rate is checked separately, against the brand's own.
    """
    return [(m.start(), m.end()) for m in _CARD_LINE_RE.finditer(text)]


def verbatim_regions(text: str):
    """Character ranges that are legitimately reproduced word for word.

    Quotations, script dialogue and card copy. Each is a fixed asset — someone's
    exact words, or text that appears on screen — so each is the brand's to
    reproduce and nobody's to reword.
    """
    return quoted_regions(text) + script_dialogue_regions(text) + card_line_regions(text)


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def find_excerpt(content: str, markers, context_chars: int = 70) -> str:
    """Return a short excerpt around the first occurrence of a marker.

    Without a concrete excerpt, the writer's revision prompt only knows a
    violation exists "somewhere" — its own stale-feedback check then can't
    verify the violation is still present and treats the feedback as
    already resolved, so nothing gets fixed across revision loops.
    """
    for marker in (markers if isinstance(markers, (list, tuple)) else [markers]):
        idx = content.find(marker)
        if idx != -1:
            start = max(0, idx - context_chars)
            end = min(len(content), idx + len(marker) + context_chars)
            excerpt = content[start:end].strip()
            return f"...{excerpt}..." if (start > 0 or end < len(content)) else excerpt
    return ""
