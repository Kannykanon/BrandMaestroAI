"""Checks on where the content came from.

Three failures of the same kind: content asserting something verifiable that
its source material does not support — an invented speaker, invented contact
details, or source text reproduced instead of rewritten.
"""
import logging
import re
from difflib import SequenceMatcher

from utils.enforcement.constants import (
    MAX_VERBATIM_SPAN_WORDS,
    MIN_AUTHORED_SPAN_WORDS,
    MIN_PHONE_DIGITS,
    MIN_QUOTED_PASSAGE_CHARS,
    QUOTE_ALTERATION_SIMILARITY,
    QUOTE_CANONICAL_COVERAGE,
)
from utils.enforcement.text import (
    about_block_regions,
    card_line_regions,
    digits,
    inline_caps_words,
    caps_word_pattern,
    quoted_regions,
    script_dialogue_lines,
    script_dialogue_regions,
    tokens_with_offsets,
    verbatim_regions,
)

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


# English grammar words, nothing domain- or brand-specific. Used only to work
# out how much of a verbatim span is language the writer actually chose.
_FUNCTION_WORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "from", "by", "with", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "this", "that", "these", "those", "he", "she", "they", "him", "her",
    "them", "his", "their", "which", "who", "whom", "also", "not", "no", "than",
    "then", "there", "here", "up", "out", "over", "after", "before", "during",
    "between", "into", "onto", "about", "has", "have", "had", "will", "would",
    "s", "t",
}




def _matching_about_regions(content: str, source: str):
    """Content "About <company>" blocks that match one in the source.

    The company's name and what it does are facts about the company, so the
    standing block that closes a release may be reproduced. Matching against the
    source's own block is what keeps that from becoming a hiding place: a word
    cap alone did not, because a sixty-word paragraph of copied prose fits
    comfortably inside the size of a real boilerplate.
    """
    source_blocks = [
        set(_quote_words(source[a:b])) for a, b in about_block_regions(source)
    ]
    if not source_blocks:
        return []

    protected = []
    for a, b in about_block_regions(content):
        words = _quote_words(content[a:b])
        if not words:
            continue
        wordset = set(words)
        # Nearly all of this block's vocabulary has to come from the source's
        # block. The genuine case is identical; copied prose under the same
        # heading shares only the company name.
        if any(
            len(wordset & block) / len(wordset) >= 0.9
            for block in source_blocks
        ):
            protected.append((a, b))
    return protected


def _name_words(source: str) -> frozenset:
    """Words the source capitalises in running text, lowercased.

    Evidence rather than a guess about which words belong to a name. A word the
    source writes as "Cascadia" is part of one; a word it writes as "contract"
    is not. Tokens the source only ever sets in full capitals are skipped, since
    those carry the same styling ambiguity being resolved here.
    """
    names = set()
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9'&.-]*", source):
        word = match.group(0)
        if word.isupper():
            continue                      # a card or a shout; not evidence
        if word[:1].isupper():
            names.add(word.lower())
    return frozenset(names)


def _authored_word_count(content: str, ctoks, i: int, j: int,
                         name_words: frozenset = frozenset()) -> int:
    """How many words in content[i:j] the writer genuinely chose.

    A proper-noun chain is a fact, not phrasing. Nobody can rewrite "the
    Cascadia International Film Festival" without getting the name wrong, and
    film publicity is full of long fixed strings — award titles, festival names,
    production companies, guild credits. A perfectly correct sentence in this
    domain can therefore share fifteen consecutive words with its source while
    containing almost no authored prose:

        "won the Grand Jury Prize for Direction at the Cascadia
         International Film Festival"

    Thirteen words, one of which ("won") is a choice. Flagging that as copying
    asks the writer to paraphrase an award, which it cannot do correctly, so the
    revision loop spends every iteration on it and the generation ends at
    MAX_ITERATIONS with a score of zero.

    Capitalised tokens are discounted as parts of names, and function words are
    discounted as grammar. What remains is what a different writer could
    legitimately have phrased differently.
    """
    span = content[ctoks[i][1]:ctoks[j - 1][2]]
    all_caps_span = not any(ch.islower() for ch in span)

    authored = 0
    for lowered, start, end in ctoks[i:j]:
        word = content[start:end]

        if all_caps_span:
            # In a wholly capitalised span — a trailer card, a headline —
            # capitalisation is styling and says nothing about whether a word is
            # a name, so every word would be discounted and no card could ever be
            # reported as copied. The source's own casing settles it: a word it
            # writes capitalised in running text is part of a name, and one it
            # writes lowercase is a word somebody chose.
            if lowered in name_words:
                continue
        elif word[:1].isupper():
            continue                      # part of a name

        if lowered in _FUNCTION_WORDS:
            continue                      # grammar, not phrasing
        authored += 1
    return authored


def find_extractive_spans(content: str, source: str,
                           max_span: int = MAX_VERBATIM_SPAN_WORDS,
                           limit: int = 5,
                           name_evidence: str = None) -> list[dict]:
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

    # Every n-gram in the source counts, however often the source repeats it.
    #
    # This deliberately does NOT exempt language the brand repeats across its own
    # documents. An earlier version did, on the reasoning that an "About <company>"
    # block closing every press release is standing copy to be reused. That was
    # wrong about what the uploaded documents are: they are references the Brand
    # Brain learns a voice from, not product documents or an asset bank to paste
    # out of. Repetition across them makes a phrase characteristic of the brand's
    # writing, which is a reason to learn the pattern, not a licence to reproduce
    # the sentence.
    #
    # Facts are the exception, and they are handled by authored-word counting
    # below rather than by an exemption here: "108 minutes" and "the Cascadia
    # International Film Festival" survive because they carry almost no authored
    # wording, while a sentence carrying the same fact does not.
    src_ngrams = {tuple(stoks[i:i + n]) for i in range(len(stoks) - n + 1)}

    words = [t for t, _, _ in ctoks]

    # Tokens inside a quotation or a line of script dialogue are excluded from
    # matching outright, rather than a span being exempted afterwards only if it
    # happens to sit entirely within one.
    #
    # The old test — span start and end both inside one quoted region — missed
    # the common shape, where a span merely *passes through* a quotation. The
    # quotation's own words are in the source, so the extension walk crossed it
    # and welded the fragments on either side into one enormous span: a talent
    # bio was reported as 62 consecutive copied words, most of them a quotation
    # it was right to reproduce, with two genuinely copied clauses buried at the
    # ends. Unsplittable feedback like that cannot be acted on.
    # Words the source itself capitalises in running text: name components.
    # Needed to judge a wholly capitalised span, where the draft's own casing
    # carries no information.
    # Drawn from the widest brand text available, not just the text being
    # compared against. A trailer sheet writes "GRAND JURY PRIZE FOR
    # DIRECTION" and "CASCADIA INTERNATIONAL FILM FESTIVAL" only in capitals,
    # so within that one content type there is no evidence they are names and
    # both awards read as authored wording — the gate then asked the writer to
    # paraphrase an award. The brand's other documents write them in running
    # text, which settles it.
    name_words = _name_words(name_evidence if name_evidence is not None else source)

    protected = verbatim_regions(content) + _matching_about_regions(content, source)
    blocked = [
        any(rs <= start and end <= re_ for rs, re_ in protected)
        for _, start, end in ctoks
    ]

    spans, i = [], 0
    while i <= len(words) - n:
        if blocked[i]:
            i += 1
            continue
        if tuple(words[i:i + n]) in src_ngrams and not any(blocked[i:i + n]):
            j = i + n
            while (
                j < len(words)
                and not blocked[j]
                and tuple(words[j - n + 1:j + 1]) in src_ngrams
            ):
                j += 1
            start, end = ctoks[i][1], ctoks[j - 1][2]
            if _authored_word_count(
                content, ctoks, i, j, name_words
            ) >= MIN_AUTHORED_SPAN_WORDS:
                spans.append({"length": j - i, "text": content[start:end]})
            i = j
        else:
            i += 1

    spans.sort(key=lambda s: -s["length"])
    return spans[:limit]




# ---------------------------------------------------------------------------
# Quotation fidelity
# ---------------------------------------------------------------------------

# Only passages long enough for an alteration to mean something. A three-word
# quoted fragment is a term of art, not a claim about what somebody said.
_QUOTED_PASSAGE_RE = re.compile(
    r'"([^"]{%d,600})"|“([^”]{%d,600})”'
    % (MIN_QUOTED_PASSAGE_CHARS, MIN_QUOTED_PASSAGE_CHARS)
)

# Any quoted region, however short — used to reconstruct what the source
# actually quoted, where a fragment can be one half of a split quotation.
_ANY_QUOTED_RE = re.compile(r'"([^"]{1,600})"|“([^”]{1,600})”')

# Typographic variants that carry no meaning here. A curly apostrophe where the
# source had a straight one is not an altered quote, and flagging it would train
# the writer to "fix" something already correct.
_TYPOGRAPHIC_EQUIVALENTS = {
    "’": "'", "‘": "'", "ʼ": "'",
    "—": "-", "–": "-", "−": "-",
    "…": "...",
    " ": " ",
}

_QUOTE_WORD_RE = re.compile(r"[\w']+")

# A source quotation broken around its attribution — `"...muffled," said
# Okpara. "It isn't...` — is one quotation, and copy that reproduces it whole
# has not merged two separate statements. Quoted regions closer together than
# this are treated as one quotation for matching.
_ATTRIBUTION_GAP_CHARS = 70


def _normalise_typography(text: str) -> str:
    for src, dst in _TYPOGRAPHIC_EQUIVALENTS.items():
        text = text.replace(src, dst)
    return text


def _quote_words(text: str) -> list[str]:
    """Words of a quotation, ignoring case and punctuation.

    Comparison happens on words because that is what a quotation asserts. A
    period moved outside the closing mark, or a comma the source placed
    differently, is a typesetting difference; "it is not" where the speaker said
    "it isn't" is a different sentence.
    """
    return _QUOTE_WORD_RE.findall(_normalise_typography(text).lower())


def _source_quotation_groups(grounding_text: str):
    """Quotations in the source, as (merged, individual) candidate lists.

    Each entry is (words, display_text), so a finding can show the wording the
    writer should have used.

    Both lists are needed. Merged groups let copy that reproduces a split
    quotation whole — dropping only the attribution between its halves — match
    exactly. Individual regions let a short altered quote be recognised: matched
    only against the long merged group it belongs to, an eight-word rewrite is
    diluted by forty words of unaltered text and scores below the threshold.
    """
    regions = []
    for m in _ANY_QUOTED_RE.finditer(grounding_text):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        regions.append((m.start(), m.end(), body))

    groups: list[list[tuple[int, int, str]]] = []
    for region in regions:
        if groups and region[0] - groups[-1][-1][1] <= _ATTRIBUTION_GAP_CHARS:
            groups[-1].append(region)
        else:
            groups.append([region])

    merged = []
    for group in groups:
        display = " ".join(part[2].strip() for part in group)
        words = _quote_words(display)
        if words:
            merged.append((words, re.sub(r"\s+", " ", display).strip()))

    individual = []
    for _, _, body in regions:
        words = _quote_words(body)
        if len(words) >= 4:      # too short to judge an alteration against
            individual.append((words, re.sub(r"\s+", " ", body).strip()))

    # Script dialogue is deliberately NOT a candidate here. It is no longer
    # exempt from the copying gate, so asking for it to be reproduced exactly
    # would set the two checks against each other: one demanding the line be
    # rewritten, the other demanding it be preserved, with the writer alternating
    # between them until the rounds ran out. Dialogue in a reference is writing
    # to learn from, so there is nothing for a fidelity check to protect.

    return merged, individual


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _sentence_candidates(quotation_entries) -> list[str]:
    """Individual sentences of the source quotations.

    A quote can be altered in one sentence and exact in the rest. Matched only
    as a whole, that alteration is averaged away by the untouched remainder, so
    the sentence is the unit that finds it.
    """
    out = []
    for _, display in quotation_entries:
        for sentence in _SENTENCE_SPLIT_RE.split(display):
            words = _quote_words(sentence)
            if len(words) >= 5:
                out.append(re.sub(r"\s+", " ", sentence).strip())
    return out


def _contains_subsequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    first = needle[0]
    span = len(needle)
    for i in range(len(haystack) - span + 1):
        if haystack[i] == first and haystack[i:i + span] == needle:
            return True
    return False


# Expanding contractions gives a form in which "It isn't" and "It is not" are
# the same word sequence. Comparing on it isolates the alteration this gate was
# built for: a quotation restyled into the brand's register, which is a change
# of wording and nothing else.
#
# The apostrophe-s case is ambiguous — possessive or "is" — but both sides are
# expanded identically, so an ambiguity can only make two passages compare as
# equal, never as different. It cannot produce a false accusation.
_CONTRACTION_SUFFIXES = (
    ("n't", "not"), ("'re", "are"), ("'ve", "have"), ("'ll", "will"),
    ("'d", "would"), ("'m", "am"), ("'s", "is"),
)


def _canonical_words(words: list[str]) -> list[str]:
    """Word sequence with contractions expanded, for contraction-blind compare."""
    out = []
    for word in words:
        for suffix, expansion in _CONTRACTION_SUFFIXES:
            if word.endswith(suffix) and len(word) > len(suffix):
                stem = word[: -len(suffix)]
                # "won't" and "can't" do not expand by stripping the suffix.
                if suffix == "n't" and stem in ("wo", "ca", "sha"):
                    stem = {"wo": "will", "ca": "can", "sha": "shall"}[stem]
                out.extend([stem, expansion])
                break
        else:
            out.append(word)
    return out


def _best_covering_candidate(candidates, words: list[str], min_coverage: float):
    """Candidate that accounts, in order, for nearly all of `words`.

    Coverage rather than similarity, because a quotation is often only part of a
    longer source one. Comparing a twenty-word excerpt against the thirty-word
    quotation it came from scores badly on similarity for a reason that has
    nothing to do with fidelity — the source simply continues. What matters is
    whether everything in the draft is accounted for by the source.

    Ties go to the shortest candidate, so the wording reported back is the tight
    quotation rather than a whole merged block of dialogue.
    """
    best = None
    for cand_words, display in candidates:
        matcher = SequenceMatcher(None, words, cand_words, autojunk=False)
        matched = sum(block.size for block in matcher.get_matching_blocks())
        coverage = matched / len(words) if words else 0.0
        if coverage < min_coverage:
            continue
        if best is None or len(cand_words) < len(best[0]):
            best = (cand_words, display, coverage)
    return best


def find_altered_quotations(content: str, grounding_text: str,
                            min_similarity: float = QUOTE_ALTERATION_SIMILARITY,
                            limit: int = 5) -> list[dict]:
    """Quoted passages that nearly match a source quotation but are not it.

    Putting words inside quotation marks and attributing them is a claim that
    they are that person's words. Changing them is a misquotation — a factual
    error about a real person, not a style slip — and it is the one defect here
    that would embarrass a publicist in front of the person quoted.

    The writer produces these while doing what it was asked: matching brand
    voice. Given a house style, it restyles the quotations too.

        source:    "It isn't. It's loud, and it's close, and it's mostly your
                    own body."
        generated: "It is not. It is loud. It is close. It is mostly your own
                    body."

    Wording and punctuation both changed, inside quotation marks, attributed to
    a named sound designer.

    Reproducing a quotation exactly passes, and so does quoting part of one,
    which is normal practice: a passage whose words appear in order inside a
    source quotation is accepted. Only a passage recognisably close to a source
    quotation without matching it is flagged, and the finding carries the
    correct wording so the writer can restore it rather than guess.

    A quoted passage resembling nothing in the source is not this failure. It is
    either invented, which find_unverified_quote_attributions covers, or it is
    ordinary quoted phrasing the brand chose itself.
    """
    if not grounding_text.strip():
        return []

    merged, individual = _source_quotation_groups(grounding_text)
    if not merged:
        return []
    sentence_pool = _sentence_candidates(merged)

    # Passages in this draft that assert somebody's exact words. Quoted text
    # only: a character's line is the draft's own writing now, not a claim about
    # what a real person said.
    candidates = [
        (m.group(1) if m.group(1) is not None else m.group(2))
        for m in _QUOTED_PASSAGE_RE.finditer(content)
    ]

    findings, seen = [], set()
    for body in candidates:
        words = _quote_words(body)
        if len(words) < 4:
            continue

        # Exact, whole or partial: the words appear in order in some source
        # quotation. Nothing has been altered.
        # Exact, whole or partial: the words appear in order inside some source
        # quotation. Checked against merged groups too, so reproducing a split
        # quotation without its attribution still counts as exact.
        if any(_contains_subsequence(src_words, words) for src_words, _ in merged):
            continue

        key = " ".join(words)
        if key in seen:
            continue

        # Faithful once contractions are expanded, but not as written: the
        # quotation was contracted or de-contracted and nothing else. Caught
        # deterministically rather than by similarity, because the restyled form
        # often breaks one sentence into several short ones — "It isn't. It's
        # loud, and it's close" becoming "It is not. It is loud. It is close." —
        # which drags the whole-passage ratio below any sane threshold and leaves
        # every fragment too short to judge on its own.
        canonical = _canonical_words(words)
        canonical_candidates = [
            (_canonical_words(cand_words), display)
            for cand_words, display in merged + individual
        ]
        match = _best_covering_candidate(
            canonical_candidates, canonical, QUOTE_CANONICAL_COVERAGE
        )
        if match is not None:
            seen.add(key)
            findings.append({
                "quoted": re.sub(r"\s+", " ", body).strip(),
                "source": match[1],
                "similarity": round(match[2], 3),
            })
            continue

        best_display, best_ratio = None, 0.0
        for src_words, display in merged + individual:
            ratio = SequenceMatcher(None, key, " ".join(src_words)).ratio()
            if ratio > best_ratio:
                best_display, best_ratio = display, ratio

        if best_display is not None and best_ratio >= min_similarity:
            seen.add(key)
            findings.append({
                "quoted": re.sub(r"\s+", " ", body).strip(),
                "source": best_display,
                "similarity": round(best_ratio, 3),
            })
            continue

        # The whole passage did not resemble any one source quotation closely
        # enough, which happens when only part of it was altered. Check each
        # sentence, so a single reworded sentence inside an otherwise faithful
        # quote is still found.
        for sentence in _SENTENCE_SPLIT_RE.split(body):
            s_words = _quote_words(sentence)
            if len(s_words) < 5:
                continue
            if any(_contains_subsequence(src_words, s_words) for src_words, _ in merged):
                continue

            s_key = " ".join(s_words)
            if s_key in seen:
                continue

            s_best, s_ratio = None, 0.0
            for candidate in sentence_pool:
                ratio = SequenceMatcher(None, s_key, " ".join(_quote_words(candidate))).ratio()
                if ratio > s_ratio:
                    s_best, s_ratio = candidate, ratio

            if s_best is not None and s_ratio >= min_similarity:
                seen.add(s_key)
                findings.append({
                    "quoted": re.sub(r"\s+", " ", sentence).strip(),
                    "source": s_best,
                    "similarity": round(s_ratio, 3),
                })

    return findings[:limit]


def source_quotation_for_span(span_text: str, grounding_text: str) -> str | None:
    """The source quotation a copied span reproduces, if it is one.

    Copying a quotation verbatim is not the failure the extractive gate exists
    for — the failure is doing it without quotation marks, which turns somebody's
    statement into unattributed prose. The remedy is punctuation, not rewriting,
    and the two need telling apart because the usual instruction ("keep the
    fact, discard the wording") is the one thing that must not be done to a
    quotation.
    """
    merged, _ = _source_quotation_groups(grounding_text)
    words = _quote_words(span_text)
    if len(words) < 4:
        return None
    for cand_words, display in merged:
        if _contains_subsequence(cand_words, words):
            return display
    return None


# find_fabricated_dialogue() used to live here, rejecting a line attributed to a
# character who never said it. It was right for publicity about an existing film
# and wrong for what this system is for: given a director's scripts and a new
# subject, writing dialogue nobody has said yet is the product, not a
# hallucination. Keeping it would also have deadlocked against the copying gate —
# reference dialogue may not be reused, invented dialogue was not allowed, so no
# dialogue could be written at all.
#
# What still holds: a line reproduced from a reference is caught by
# find_extractive_spans, and a real person's quotation is caught by
# find_altered_quotations.


def find_unbranded_emphasis_caps(content: str, grounding_text: str,
                                 limit: int = 8) -> list[dict]:
    """Words shouted in capitals mid-sentence that the brand never capitalises.

    The measured all-caps rate cannot see this. Harbor Line's social copy runs
    4.4 all-caps words per 100, and a draft that wrote

        SALVAGE won the GRAND Jury Prize ... an EXCLUSIVE commentary track ...
        Experience its ACCLAIMED sound design

    measured 5.9 — comfortably inside tolerance, and approved. The rate was
    right and every choice was wrong: the brand's capitals are its title and its
    card lines, never an adjective it wants to lean on.

    So the question is not how many but which. A word the brand itself sets in
    capitals somewhere in its own material is that brand's convention; a word it
    never does is the model reaching for emphasis. Words on a capitals-only line
    are exempt, because there the capitals are structural — a card, a heading, a
    label — and the brand's rate check governs those.
    """
    if not grounding_text.strip():
        return []

    permitted = {word.upper() for word, _ in inline_caps_words(grounding_text)}
    permitted |= {
        grounding_text[a:b].upper()
        for a, b in card_line_regions(grounding_text)
    }
    # Individual words of a card line count too: a title that only ever appears
    # on a card of its own is still the brand's own capitalisation.
    for a, b in card_line_regions(grounding_text):
        permitted |= {w.upper() for w in re.findall(r"[A-Za-z0-9'&.-]+", grounding_text[a:b])}

    findings, seen = [], set()
    for word, _offset in inline_caps_words(content):
        upper = word.upper()
        if upper in permitted or upper in seen:
            continue
        seen.add(upper)
        findings.append({"word": word})

    return findings[:limit]


def sanitize_unbranded_emphasis_caps(content: str, grounding_text: str
                                     ) -> tuple[str, list[str]]:
    """Lowercase mid-sentence capitals the brand does not use, in place.

    Same argument as sanitize_banned_punctuation: "this brand does not shout"
    is mechanical and unambiguous, so it deserves a mechanical fix with a
    guaranteed outcome rather than a revision round with a probabilistic one.

    Bouncing the draft did not work. Social copy was corrected at round two for
    EXCLUSIVE, GRAND, NOT and UNIQUE, reached 7.9 by round five, and then shouted
    PERCEPTION, NOT, UNIQUE and NOW at round six — a fresh set of words each
    time, with the constraint listed in front of it. The writer was not
    reintroducing the same violation, so telling it what it had already fixed
    could not help.

    Which words are permitted comes from the brand's own material, so nothing
    here is specific to any one brand. A word at the start of a sentence keeps
    its initial capital.
    """
    findings = find_unbranded_emphasis_caps(content, grounding_text, limit=100)
    if not findings:
        return content, []

    shouted = {f["word"].upper() for f in findings}
    fixes = []

    # How the brand itself spells each word when it is not shouting. "GRAND"
    # belongs to "Grand Jury Prize", so lowercasing it would give "grand Jury
    # Prize" — unshouted and wrong. The brand's own material is the authority on
    # the casing, and this text ships without another review round.
    brand_casing = {}
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9'&.-]*", grounding_text):
        word = match.group(0)
        if word.isupper():
            continue                  # a shout or a card line; not evidence
        brand_casing.setdefault(word.upper(), word)

    out = []
    last = 0
    for line_match in re.finditer(r"^.*$", content, re.MULTILINE):
        line = line_match.group(0)
        if not re.search(r"[a-z]", line):
            continue          # capitals-only line: structural, left alone
        for m in caps_word_pattern().finditer(line):
            start = line_match.start() + m.start()
            end = line_match.start() + m.end()
            word = content[start:end]
            if word.upper() not in shouted:
                continue
            preceding = content[:start].rstrip()
            sentence_start = (not preceding) or preceding[-1] in ".!?:\n"

            fixed = brand_casing.get(word.upper())
            if fixed is None:
                fixed = word.capitalize() if sentence_start else word.lower()
            elif sentence_start:
                fixed = fixed[0].upper() + fixed[1:]
            if fixed == word:
                continue
            out.append(content[last:start])
            out.append(fixed)
            fixes.append(f"{word} -> {fixed}")
            last = end
    out.append(content[last:])
    return "".join(out), fixes
