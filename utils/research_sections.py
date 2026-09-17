"""The banners the researcher wraps around each source, in one place.

The research blob handed to the writer is assembled from two sources, each
introduced by a banner and a note explaining how to treat it. Those notes are
the pipeline talking to itself. They are not the brand's material and nobody
should ever be asked to write from them — which is exactly what happened.

The story-coverage check reads the research as a sequence of story beats. The
banner line is capitalised, so it was correctly read as a heading; the note
under it is ordinary prose, so it became a beat, and a screenplay was told for
six consecutive rounds:

    NOT TOLD AT ALL: "Facts about the subject itself come from here. Where this
    and the external context below disagree, this wins."
    missing from the draft: context, disagree, external, fact, itself, subject, win

There is no scene there. The draft could not satisfy it, the run spent its
whole revision budget on it, and the checks that would have caught the draft's
real faults never got a turn.

So the banners live here, the researcher renders them from here, and anything
reading the research back strips them from here. One definition, so a change to
the wording cannot quietly reintroduce the bug.
"""
from __future__ import annotations

import re

SOURCE_BANNER = "═══ SOURCE MATERIAL — THE BRAND'S OWN DOCUMENTS (AUTHORITATIVE) ═══"
SOURCE_NOTE = (
    "Facts about the subject itself come from here. Where this and the\n"
    "external context below disagree, this wins."
)

EXTERNAL_BANNER = "═══ EXTERNAL CONTEXT — LIVE WEB SEARCH (SUPPORTING) ═══"
EXTERNAL_NOTE = (
    "Current market/reception context to position the piece against.\n"
    "Use it for framing and timeliness. Do NOT use it to assert facts\n"
    "about the brand's own product that the source material above does\n"
    "not already establish."
)

NO_RESEARCH = "No research available — write from the topic brief alone."

_FRAMING = (SOURCE_BANNER, SOURCE_NOTE, EXTERNAL_BANNER, EXTERNAL_NOTE, NO_RESEARCH)


def source_section(text: str) -> str:
    return f"{SOURCE_BANNER}\n{SOURCE_NOTE}\n\n{text.strip()}"


def external_section(text: str) -> str:
    return f"{EXTERNAL_BANNER}\n{EXTERNAL_NOTE}\n\n{text.strip()}"


def _lines_of(block: str) -> set:
    return {line.strip() for line in block.splitlines() if line.strip()}


_FRAMING_LINES = set().union(*(_lines_of(block) for block in _FRAMING))


def strip_framing(research: str) -> str:
    """The research with the pipeline's own notes removed, sources intact.

    Matched line by line against what this module wrote, not by pattern: a
    guess about what instructional prose looks like would eventually throw away
    a real source paragraph, and losing the brand's material is the worse
    failure of the two.
    """
    if not research:
        return research
    kept = [line for line in research.splitlines() if line.strip() not in _FRAMING_LINES]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
