"""Checks on where the content came from.

Three failures of the same kind: content asserting something verifiable that
its source material does not support — an invented speaker, invented contact
details, or source text reproduced instead of rewritten.
"""
import logging
import re
from collections import Counter

from utils.enforcement.constants import MAX_VERBATIM_SPAN_WORDS, MIN_PHONE_DIGITS
from utils.enforcement.text import digits, quoted_regions, tokens_with_offsets

logger = logging.getLogger(__name__)


# Generic corporate role-nouns, not brand names or topics — used only to tell
# a legitimate unnamed-role attribution ("said Meridian's Head of Development")
# apart from a specific invented person ("said Sarah Chen, Head of Studio").
# The former is a real, common brand pattern; the latter is a hallucinated
# identity. This list is about English business vocabulary, not any one
# brand's content, so it holds regardless of which documents get uploaded.
_ROLE_INDICATOR_WORDS = {
    "head", "director", "officer", "president", "ceo", "coo", "cto", "cfo",
    "chief", "manager", "spokesperson", "spokesman", "spokeswoman", "team",
    "department", "communications", "studio", "studios", "group", "committee",
    "board", "founder", "chairman", "chairwoman", "chairperson", "lead", "vp",
}

_ATTRIBUTION_VERBS = (
    r"(?:said|says|stated|states|explains|explained|noted|notes|added|"
    r"remarked|shared|according to)"
)
_NAME_SPAN = r"[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3}"
_QUOTE_THEN_ATTRIBUTION = re.compile(
    r'"([^"]{15,400})"\s*[,]?\s*' + _ATTRIBUTION_VERBS + r"\s+(" + _NAME_SPAN + r")"
)
_ATTRIBUTION_THEN_QUOTE = re.compile(
    r"(" + _NAME_SPAN + r")\s*[,]?\s*" + _ATTRIBUTION_VERBS + r'\s*[,:]?\s*"([^"]{15,400})"'
)


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"\b[a-z0-9][a-z0-9-]{1,62}\.(?:com|net|org|io|co|tv|film|studio|news|app|ai|uk|us|ca)\b",
    re.IGNORECASE,
)
# Loose on shape, strict on length: a real phone number carries at least 9
# digits, which keeps dates, episode counts and running times out of it.
#
# Separators are capped at two consecutive characters — enough for the ") " in
# "(555) 123-4567" — and a period followed by whitespace is excluded, because
# that is a sentence boundary rather than a separator. Without that exclusion
# "Episode 4. 2026-09-07" reads as a nine-digit phone number.
_PHONE_RE = re.compile(r"\(?\+?\d(?:(?:(?!\.\s)[\s().\-]){0,2}\d){8,}")


def find_ungrounded_contact_details(content: str, grounding_text: str) -> list[dict]:
    """Flag email addresses, phone numbers and URLs that appear nowhere in source.

    Denied a placeholder to fill, the writer invented one: a press release that
    previously shipped "[Press Contact Name]" came back with
    "press@meridianstudios.com" and "(555) 123-4567" instead — neither of which
    exists in any uploaded document. That is worse than the placeholder it
    replaced, because an unfilled slot is visibly unfinished while a plausible
    press address looks publishable.

    The numeric hallucination check does not cover these: a contact address is
    not a claim about brand experience, so it was never in scope. Checked
    deterministically because "does this exact string appear in the source" is
    an exact-match question.
    """
    if not grounding_text.strip():
        return []

    grounding_lower = grounding_text.lower()
    grounding_digits = digits(grounding_text)
    findings, seen = [], set()

    def flag(value, kind):
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "value": value,
            "kind": kind,
            "message": (
                f"Invented {kind} {value!r} — it does not appear in any uploaded "
                "document or research result. Never fabricate contact details. "
                "Use the brand's own convention for directing enquiries instead, "
                "or omit the line."
            ),
        })

    emails = _EMAIL_RE.findall(content)
    for email in emails:
        if email.lower() not in grounding_lower:
            flag(email, "email address")

    # Strip emails before domain matching so one address is not reported twice.
    without_emails = _EMAIL_RE.sub(" ", content)

    for url in _URL_RE.findall(without_emails):
        cleaned = url.rstrip(".,;:)")
        if cleaned.lower() not in grounding_lower:
            flag(cleaned, "URL")

    for domain in _BARE_DOMAIN_RE.findall(without_emails):
        if domain.lower() not in grounding_lower:
            flag(domain, "web address")

    for phone in _PHONE_RE.findall(content):
        phone_digits = digits(phone)
        if len(phone_digits) >= MIN_PHONE_DIGITS and phone_digits not in grounding_digits:
            flag(phone.strip(), "phone number")

    return findings[:6]


def find_unverified_quote_attributions(content: str, grounding_text: str) -> list[dict]:
    """
    Catch a specific hallucination shape the numeric-claims check above never
    looks at: a quoted statement attributed to a named individual who does not
    exist anywhere in this generation's actual source material. E.g. a press
    release inventing "said Sarah Chen, Head of Studio" when no such person
    appears in the uploaded documents or researched context. Deterministic and
    topic/brand-agnostic — it only flags a NAME-shaped attribution (skipping
    generic unnamed role attributions via _ROLE_INDICATOR_WORDS) that is
    verifiably absent from the grounding text, so it works for any brand's
    documents without hardcoding who the "real" people are.
    """
    if not grounding_text.strip():
        return []  # nothing to verify against — skip rather than flag everything

    grounding_lower = grounding_text.lower()
    findings = []
    seen = set()

    for pattern, quote_group, who_group in (
        (_QUOTE_THEN_ATTRIBUTION, 1, 2),
        (_ATTRIBUTION_THEN_QUOTE, 2, 1),
    ):
        for m in pattern.finditer(content):
            # rstrip trailing punctuation the name-span regex can sweep up
            # (e.g. a sentence-ending period right after the name).
            who = m.group(who_group).strip().rstrip(".,;:")
            quote = m.group(quote_group).strip()
            key = who.lower()
            if key in seen:
                continue
            words = re.findall(r"[a-z']+", key)
            if any(w in _ROLE_INDICATOR_WORDS for w in words):
                continue  # unnamed role attribution — a normal brand pattern
            if key in grounding_lower:
                continue  # this name genuinely appears in the source material
            seen.add(key)
            findings.append({"who": who, "quote": quote})

    return findings


def find_extractive_spans(content: str, source: str,
                           max_span: int = MAX_VERBATIM_SPAN_WORDS,
                           limit: int = 5) -> list[dict]:
    """Runs of more than `max_span` consecutive words copied verbatim from source.

    Catches the failure mode where the writer summarises its research instead of
    writing from it — reproducing the source's phrasing (and, with it, whatever
    internal material the source happened to contain) rather than re-expressing
    the facts in the brand's own voice.

    Two kinds of verbatim reuse are legitimate and exempt: direct quotations,
    which have to match their source, and language the brand repeats across its
    own documents, which is standing copy rather than lifted research.
    """
    ctoks = tokens_with_offsets(content)
    stoks = [t for t, _, _ in tokens_with_offsets(source)]
    n = max_span + 1
    if len(ctoks) < n or len(stoks) < n:
        return []

    # Only n-grams that appear ONCE in the source count as copying.
    #
    # An n-gram the brand repeats across its own retrieved documents is standing
    # language it reuses on purpose — the "About <company>" boilerplate that
    # closes every press release, a recurring logline, a tagline, a rights
    # statement. Reproducing that verbatim is correct, and required: a
    # distributor's boilerplate is not supposed to be paraphrased differently in
    # each release.
    #
    # Without this the gate flagged Harbor Line's own boilerplate as plagiarism
    # of itself, sent the draft back for it, and burned every revision iteration
    # on a violation the writer could not legitimately fix — ending at
    # MAX_ITERATIONS with a score of zero on otherwise publishable copy.
    #
    # A distinctive passage lifted from one source document still appears once,
    # so the failure this gate exists for is unaffected.
    counts = Counter(
        tuple(stoks[i:i + n]) for i in range(len(stoks) - n + 1)
    )
    src_ngrams = {ng for ng, c in counts.items() if c == 1}

    quoted = quoted_regions(content)
    words = [t for t, _, _ in ctoks]

    def inside_quote(a, b):
        return any(qs <= a and b <= qe for qs, qe in quoted)

    spans, i = [], 0
    while i <= len(words) - n:
        if tuple(words[i:i + n]) in src_ngrams:
            j = i + n
            while j < len(words) and tuple(words[j - n + 1:j + 1]) in src_ngrams:
                j += 1
            start, end = ctoks[i][1], ctoks[j - 1][2]
            if not inside_quote(start, end):
                spans.append({"length": j - i, "text": content[start:end]})
            i = j
        else:
            i += 1

    spans.sort(key=lambda s: -s["length"])
    return spans[:limit]
