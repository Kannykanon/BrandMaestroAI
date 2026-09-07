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
