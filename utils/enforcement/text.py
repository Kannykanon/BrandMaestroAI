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


_CAPS_WORD_RE = re.compile(
    r"(?<![A-Za-z])[A-Z][A-Z0-9]*(?:['&.-][A-Z0-9]+)*(?![A-Za-z])"
)


def caps_word_pattern():
    """The compiled all-caps word pattern, for callers that scan lines."""
    return _CAPS_WORD_RE


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

    NOT exempt from the extractive-copying check. Dialogue in an uploaded
    document is the reference author's writing, and the writing is the one thing
    the system is supposed to learn from rather than reuse: feed it a director's
    scripts and ask for a new script, and a line lifted from one of them is
    plagiarism, not a fact. Facts about the new subject come from the research.

    Retained because the fidelity and fabrication checks locate dialogue lines,
    and because a trailer sheet's own dialogue section needs recognising as
    structure.
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


# The trailing "About <company>" block that closes a press release. The heading
# names the company, so the pattern is a convention rather than anything
# specific to one brand: "About" followed by a capitalised name, on its own line,
# optionally wrapped in markdown emphasis.
_ABOUT_HEADING_RE = re.compile(
    r"^[*_#\s]*About\s+[A-Z][^\n]{0,80}$",
    re.MULTILINE,
)

# Enough for a company description and no more, so the exemption cannot be used
# to shelter arbitrary copied text under an "About" heading.
# A real boilerplate paragraph is thirty to fifty words. Kept close to that:
# at 140 an entire page of copied prose fitted under the heading and went
# unflagged, which turned the exemption into a hiding place.
_ABOUT_BLOCK_MAX_WORDS = 70


def about_block_regions(text: str):
    """Character ranges of a trailing "About <company>" boilerplate block.

    The company's name and what it does — its territories, how many titles a
    year — are facts about the company, and a release that omits them is
    missing something it is supposed to carry. Treated as factual standing
    content rather than phrasing to be reinvented on each release.

    Bounded: the block ends at the release-end marker, at a heading that starts
    something else, or after _ABOUT_BLOCK_MAX_WORDS.
    """
    regions = []
    for heading in _ABOUT_HEADING_RE.finditer(text):
        start = heading.start()
        end = len(text)
        words = 0
        for line in re.finditer(r"^.*$", text[heading.end():], re.MULTILINE):
            absolute = heading.end() + line.end()
            stripped = line.group(0).strip()
            if stripped in ("###", "-30-", "END", "ENDS"):
                break
            if stripped and _ABOUT_HEADING_RE.match(line.group(0)):
                break
            # Checked before extending, not after. A boilerplate paragraph is
            # usually a single unwrapped line, so counting the line and then
            # testing the cap admitted the whole paragraph however long it was —
            # which let a page of copied prose sit under the heading untouched.
            if words + len(stripped.split()) > _ABOUT_BLOCK_MAX_WORDS:
                break
            words += len(stripped.split())
            end = absolute
        regions.append((start, end))
    return regions


def verbatim_regions(text: str):
    """Character ranges that are legitimately reproduced word for word.

    Quotations only, and even that is narrow: a statement attributed to a named
    person is a record of what they said.

    Script dialogue is NOT here. A character's line is the reference author's
    writing — the thing the Brand Brain exists to learn the shape of, not to
    hand back. Upload a director's scripts, ask for a new one, and a line from
    the old script is plagiarism however faithfully it is reproduced.

    Card copy is not here either, for the same reason: "THE HOLD IS NOT EMPTY"
    is writing, so a new sheet arrives at its own cards.

    The trailing "About <company>" block is a fact about the company and is
    protected in find_extractive_spans, where it can be checked against the
    source's own block rather than trusted to a heading.
    """
    return quoted_regions(text)


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
