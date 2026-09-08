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


def script_dialogue_regions(text: str):
    """Character ranges of the spoken part of script-format dialogue lines.

    A line of dialogue on a trailer copy sheet is a quotation of the film. It
    cannot be paraphrased — an actor said those words — so it belongs with
    quoted text: exempt from the extractive-copying check, and protected from
    alteration by the quotation-fidelity check.
    """
    return [(m.start(1), m.end(1)) for m in _SCRIPT_LINE_RE.finditer(text)]


def verbatim_regions(text: str):
    """Character ranges that are legitimately reproduced word for word.

    Quotations and script dialogue. Both assert that somebody said exactly
    this, so both are the brand's to copy and nobody's to reword.
    """
    return quoted_regions(text) + script_dialogue_regions(text)


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
