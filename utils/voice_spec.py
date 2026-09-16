"""How a brand writes, measured from its own documents — shapes and rules, never its sentences.

The Brand Brain used to describe a voice in adjectives ("short, declarative
sentences") and pin it down with single numbers ("6.3 four-plus-syllable words
per 100"). Neither is writable from. A model told to raise a syllable rate
reaches it with "convene" and "congregation"; a model told "short sentences"
writes any short sentences at all.

This measures the shapes instead — how long sentences run, how often they are
fragments, what an opening is made of, how dialogue is introduced — and states
them as rules with ranges. It never quotes the corpus: brand documents are the
evidence, not material to paste into a prompt.

    prose_only()      strip front matter, headings, scene directions and appendices
    shape_metrics()   measure one document's shapes
    corpus_spec()     combine documents into medians and ranges
    render_spec()     the VOICE SPEC section of the Brand Brain
    beat_grammar()    what an opening, a closing and a section are made of, as shapes
"""
from __future__ import annotations

import re
import statistics
from typing import Iterable, Optional

# Lines that are not the brand's prose: the document's own framing, its
# headings, and the appendices some documents end with. Counting them is how a
# corpus of plain scene writing came out with a four-syllable rate set by
# "protagonist", "original" and "identity" — words from the notes, not the work.
_FRONT_MATTER = re.compile(
    r"^\s*(note|disclaimer|original retelling|not the original|based on|source|copyright|©|version|draft)\b",
    re.I,
)
_SEPARATOR = re.compile(r"^\s*[=\-_*#~]{3,}\s*$")
_SCENE_HEADING = re.compile(r"^\s*(INT\.|EXT\.|INT/EXT|I/E|EST\.)", re.I)
_TRANSITION = re.compile(
    r"^\s*(FADE IN|FADE OUT|FADE TO BLACK|CUT TO|SMASH CUT|DISSOLVE TO|MATCH CUT|INTERCUT|THE END)\b",
    re.I,
)
_ACT_OR_SECTION = re.compile(r"^\s*(ACT\b|PART\b|CHAPTER\b|SCENE\b|#{1,6}\s)", re.I)
# Sections that comment on the work rather than being it.
_APPENDIX = re.compile(
    r"^\s*#*\s*(structural lesson|lesson|summary|analysis|notes?|takeaways?|appendix|about this)\b",
    re.I,
)
_LABEL_LINE = re.compile(r"^\s*([A-Z][A-Z .'\-]{1,30}):\s*\S")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z0-9']+")
_NOMINALISATION = re.compile(r"\b\w{4,}(?:tion|sion|ment|ity|ance|ence|ness|ism|ivity)\b", re.I)
_CONTRACTION = re.compile(r"\b\w+['’](?:t|s|re|ve|ll|d|m)\b", re.I)
_OPENERS = ("he", "she", "they", "it", "we", "you", "i", "his", "her", "their")


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _SEPARATOR.match(stripped) or _ACT_OR_SECTION.match(stripped) or _SCENE_HEADING.match(stripped):
        return True
    if _TRANSITION.match(stripped):
        return True
    letters = [c for c in stripped if c.isalpha()]
    # A short line in capitals is a title or a card, not a sentence.
    return bool(letters) and all(c.isupper() for c in letters) and len(stripped.split()) <= 12


def body_lines(text: str) -> list[str]:
    """The document without its framing or its appendix, headings and all.

    Keeps scene headings and transitions, because the shape of an opening is
    partly made of them; prose_only() drops those as well.
    """
    kept, started = [], False
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        # A lesson or notes *heading* ends the document's writing.
        if _APPENDIX.match(stripped) and (_is_heading(line) or stripped.startswith("#")):
            break
        # A note anywhere ("NOTE: this is an original retelling") is framing,
        # whether it sits above the title or below it.
        if _FRONT_MATTER.match(stripped) or _SEPARATOR.match(stripped):
            continue
        if _is_heading(line):
            started = True  # the framing block ends at the first heading
            kept.append(stripped)
            continue
        if not started and index < 3:
            continue
        kept.append(stripped)
    return kept


def prose_only(text: str) -> str:
    """The document's own writing: no front matter, headings, scene directions or appendices."""
    return "\n".join(line for line in body_lines(text) if not _is_heading(line)).strip()


def sentences(text: str) -> list[str]:
    out = []
    for block in text.splitlines():
        block = block.strip()
        if not block:
            continue
        out += [s.strip() for s in _SENTENCE_SPLIT.split(block) if s.strip()]
    return out


def _share(count: int, total: int) -> float:
    return round(100.0 * count / total, 1) if total else 0.0


def shape_metrics(text: str) -> dict:
    """The shapes of one document's prose. Rates are per 100 words, shares per 100 sentences."""
    words = _WORD.findall(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    sents = sentences(text)
    total_words, total_sents = len(words), len(sents)
    if total_words < 30 or not total_sents:
        return {}
    lengths = [len(_WORD.findall(s)) for s in sents]
    per100 = lambda n: round(100.0 * n / total_words, 2)
    dialogue_lines = sum(1 for l in lines if _LABEL_LINE.match(l))
    return {
        "words": total_words,
        "sentences": total_sents,
        "mean_words_per_sentence": round(statistics.mean(lengths), 1),
        "median_words_per_sentence": round(statistics.median(lengths), 1),
        "share_sentences_under_6_words": _share(sum(1 for n in lengths if n < 6), total_sents),
        "share_sentences_over_20_words": _share(sum(1 for n in lengths if n > 20), total_sents),
        "share_sentences_opening_with_pronoun": _share(
            sum(1 for s in sents if s.split() and s.split()[0].strip(",.:;\"'").lower() in _OPENERS), total_sents),
        "share_single_sentence_paragraphs": _share(sum(1 for l in lines if len(sentences(l)) == 1), len(lines)),
        "commas_per_sentence": round(text.count(",") / total_sents, 2),
        "nominalisations_per_100_words": per100(len(_NOMINALISATION.findall(text))),
        "four_plus_syllable_words_per_100_words": per100(
            sum(1 for w in words if len(re.findall(r"[aeiouy]+", w.lower())) >= 4)),
        "contractions_per_100_words": per100(len(_CONTRACTION.findall(text))),
        "mean_word_length": round(sum(len(w) for w in words) / total_words, 2),
        "share_lines_that_are_dialogue": _share(dialogue_lines, len(lines)),
    }


def band(values: list[float]) -> dict:
    """Median and the middle half of a set of per-document measurements."""
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return {}
    if len(clean) == 1:
        return {"median": clean[0], "low": clean[0], "high": clean[0], "documents": 1}
    quantiles = statistics.quantiles(clean, n=4, method="inclusive")
    return {"median": round(statistics.median(clean), 2), "low": round(quantiles[0], 2),
            "high": round(quantiles[2], 2), "documents": len(clean)}


def corpus_spec(documents: Iterable[str]) -> dict:
    """Per-document shapes combined into medians and ranges, plus the markers the brand uses."""
    per_document, transitions, labels = [], {}, {}
    prose_total = 0
    for document in documents:
        prose = prose_only(document)
        metrics = shape_metrics(prose)
        if metrics:
            per_document.append(metrics)
            prose_total += metrics["words"]
        for line in body_lines(document):
            stripped = line.strip()
            if _TRANSITION.match(stripped):
                marker = stripped.rstrip(":.").upper()[:24]
                transitions[marker] = transitions.get(marker, 0) + 1
            match = _LABEL_LINE.match(stripped)
            if match:
                labels[match.group(1).strip()] = labels.get(match.group(1).strip(), 0) + 1
    if not per_document:
        return {}
    keys = [k for k in per_document[0] if k not in ("words", "sentences")]
    return {
        "documents": len(per_document),
        "prose_words": prose_total,
        "bands": {key: band([m[key] for m in per_document if key in m]) for key in keys},
        "transitions": sorted(transitions, key=transitions.get, reverse=True)[:6],
        "speaker_labels": sorted(labels, key=labels.get, reverse=True)[:8],
    }


def beat_grammar(documents: Iterable[str], lines_per_beat: int = 6) -> dict:
    """What an opening and a closing are made of, as a sequence of shapes rather than text."""
    def shape(line: str) -> str:
        stripped = line.strip()
        if _TRANSITION.match(stripped):
            return "transition line (e.g. FADE IN:)"
        if _SCENE_HEADING.match(stripped):
            return "scene heading (INT./EXT. PLACE — TIME)"
        if _ACT_OR_SECTION.match(stripped) or _is_heading(stripped):
            return "section or act title in capitals"
        if _LABEL_LINE.match(stripped):
            return "speaker label followed by a line of dialogue"
        count = len(_WORD.findall(stripped))
        if count <= 3:
            return f"very short sentence ({count} words)"
        if count <= 8:
            return f"short sentence ({count} words)"
        if count <= 16:
            return f"medium sentence ({count} words)"
        return f"long sentence ({count} words)"

    openings, closings = [], []
    for document in documents:
        lines = [l.strip() for l in body_lines(document) if l.strip()]
        if not lines:
            continue
        body_start = next((i for i, l in enumerate(lines) if _TRANSITION.match(l) or _SCENE_HEADING.match(l)), 0)
        openings.append([shape(l) for l in lines[body_start:body_start + lines_per_beat]])
        closings.append([shape(l) for l in lines[-lines_per_beat:]])

    def common(sequences: list[list[str]]) -> list[str]:
        if not sequences:
            return []
        length = min(len(s) for s in sequences)
        result = []
        for position in range(length):
            options = [s[position] for s in sequences]
            result.append(max(set(options), key=options.count))
        return result

    return {"opening": common(openings), "closing": common(closings)}


def _rule_lines(spec: dict) -> list[str]:
    """Rules a writer can follow, derived from the bands."""
    bands, rules = spec.get("bands", {}), []
    def value(key, field="median"):
        return bands.get(key, {}).get(field)

    median_length = value("median_words_per_sentence")
    if median_length is not None:
        rules.append(f"Sentences run about {median_length:.0f} words (typical range "
                     f"{value('median_words_per_sentence', 'low'):.0f}–{value('median_words_per_sentence', 'high'):.0f}).")
    short = value("share_sentences_under_6_words")
    if short is not None:
        rules.append(f"About {short:.0f} in 100 sentences are under six words. Let sentences end early.")
    long_share = value("share_sentences_over_20_words")
    if long_share is not None and long_share < 5:
        rules.append("Sentences over twenty words are rare; break them instead of joining them.")
    paragraphs = value("share_single_sentence_paragraphs")
    if paragraphs is not None and paragraphs > 50:
        rules.append(f"Most paragraphs are a single sentence ({paragraphs:.0f} in 100 lines).")
    nominal = value("nominalisations_per_100_words")
    if nominal is not None:
        rules.append(
            f"Abstract nouns (-tion, -ment, -ance, -ity) are {'rare' if nominal < 3 else 'common'}: about "
            f"{nominal:.1f} per 100 words. Prefer the verb: write what someone does, not the name of the doing."
            if nominal < 3 else
            f"Abstract nouns appear about {nominal:.1f} per 100 words.")
    long_words = value("four_plus_syllable_words_per_100_words")
    if long_words is not None:
        rules.append(
            f"Long words (four syllables or more) appear about {long_words:.1f} per 100 words, and they are the "
            f"subject's own words, not fancier ways to say plain ones. Never reach for a longer word to raise a rate.")
    contractions = value("contractions_per_100_words")
    if contractions is not None and contractions < 1:
        rules.append("Contractions are rare outside dialogue.")
    pronouns = value("share_sentences_opening_with_pronoun")
    if pronouns is not None and pronouns > 30:
        rules.append(f"{pronouns:.0f} in 100 sentences open with a pronoun ('He', 'They'), stating the subject first.")
    dialogue = value("share_lines_that_are_dialogue")
    if dialogue is not None and dialogue > 3:
        rules.append(f"About {dialogue:.0f} in 100 lines are dialogue, introduced by a speaker label.")
    return rules


def render_spec(spec: dict, grammar: Optional[dict] = None) -> str:
    """The VOICE SPEC section: how this brand writes, as rules and shapes."""
    if not spec:
        return ""
    lines = [
        "# VOICE SPEC (measured from this brand's own writing)",
        "How this brand builds sentences, measured over its documents' prose — front matter, headings, scene "
        "directions and appendices excluded. These are habits to write with, not numbers to hit: match the shapes "
        "and the rates follow. Never lengthen or shorten a word to move a number.",
        "",
        "SENTENCE AND PARAGRAPH HABITS:",
    ]
    lines += [f"- {rule}" for rule in _rule_lines(spec)]
    if grammar and grammar.get("opening"):
        lines += ["", "OPENING IS BUILT AS:"] + [f"{i}. {shape}" for i, shape in enumerate(grammar["opening"], 1)]
    if grammar and grammar.get("closing"):
        lines += ["", "CLOSING IS BUILT AS:"] + [f"{i}. {shape}" for i, shape in enumerate(grammar["closing"], 1)]
    if spec.get("transitions"):
        lines += ["", f"TRANSITION MARKERS USED: {', '.join(spec['transitions'])}"]
    if spec.get("speaker_labels"):
        lines += [f"LINE LABELS USED: {', '.join(spec['speaker_labels'][:6])}"]
    lines += ["", f"MEASURED OVER: {spec.get('documents', 0)} document(s), {spec.get('prose_words', 0)} words of prose."]
    return "\n".join(lines)


def longest_shared_run(text: str, corpus: str) -> int:
    """The longest run of consecutive words this text shares with the corpus."""
    def words_of(value: str) -> list[str]:
        return [w.lower() for w in _WORD.findall(value)]

    draft, source = words_of(text), words_of(corpus)
    if not draft or not source:
        return 0
    source_starts: dict[str, list[int]] = {}
    for index, word in enumerate(source):
        source_starts.setdefault(word, []).append(index)
    longest = 0
    for i, word in enumerate(draft):
        for j in source_starts.get(word, ()):
            run = 0
            while i + run < len(draft) and j + run < len(source) and draft[i + run] == source[j + run]:
                run += 1
            longest = max(longest, run)
    return longest


# ---------------------------------------------------------------------------
#  Comparing a draft with the brand
# ---------------------------------------------------------------------------
# What each shape means when a draft sits outside the brand's own range. The
# note says what the writing is doing, never a number to hit: told to raise a
# four-syllable rate, a model reaches it with "convene" and "congregation".
_DIAGNOSTIC_NOTES = {
    "median_words_per_sentence": (
        "Sentences are shorter than the brand's. They end before they say anything; let some run on to the "
        "detail that matters.",
        "Sentences are longer than the brand's. Split them: this brand states one thing per sentence."),
    "nominalisations_per_100_words": (
        "Fewer abstract nouns than the brand uses — usually fine; only a worry if the writing has gone telegraphic.",
        "More abstract nouns than the brand uses. It is naming actions instead of performing them: "
        "'demonstrates profound deference' where this brand writes 'bows'."),
    "four_plus_syllable_words_per_100_words": (
        "Plainer words than the brand's. Fine unless facts have been dropped to get there.",
        "Longer words than the brand uses. Check each one is the subject's own word rather than a longer way "
        "of saying a plain one."),
    "share_sentences_under_6_words": (
        "Fewer very short sentences than the brand writes. Some beats should land in three words.",
        "More very short sentences than the brand writes. Strung together they read as a list, not a scene."),
    "contractions_per_100_words": (
        "Fewer contractions than the brand uses; the writing has gone formal.",
        "More contractions than the brand uses."),
}


def _band_from_metrics(metrics: str, key: str) -> Optional[dict]:
    values = {}
    for suffix, field in (("", "median"), ("_low", "low"), ("_high", "high")):
        match = re.search(rf"^-\s*{re.escape(key)}{suffix}:\s*([0-9.]+)\s*$", metrics, re.M)
        if match:
            values[field] = float(match.group(1))
    if "median" not in values:
        return None
    values.setdefault("low", values["median"])
    values.setdefault("high", values["median"])
    return values



def _enough_evidence(measured: dict, key: str, actual: float, low: float, high: float) -> bool:
    """Whether a rate difference is more than the noise of a short draft.

    Counts vary with the square root of their own size, so a draft of ninety
    words that happens to contain two abstractions is 2.2 per 100 against a
    brand's 0.9 and means nothing. The same rule the mechanics check uses.
    """
    words = measured.get("words", 0)
    if not words:
        return False
    target = low if actual < low else high
    expected = target * words / 100.0
    seen = actual * words / 100.0
    bar = max(2.0, 2.0 * (expected ** 0.5))
    return abs(seen - expected) >= bar

def voice_diagnostics(draft: str, metrics: str) -> list[str]:
    """Where a draft's shapes sit outside the brand's own range, in words rather than targets.

    Advisory by design. These used to be blocking rules with a number attached,
    and a draft chased that number round the revision loop: 2.9, then 23.5, then
    1.5, then padded with long words until it passed and read like nobody.
    """
    prose = prose_only(draft) or draft
    measured = shape_metrics(prose)
    if not measured:
        return []
    notes = []
    for key, (below, above) in _DIAGNOSTIC_NOTES.items():
        band_values = _band_from_metrics(metrics, key)
        if not band_values or key not in measured:
            continue
        actual, low, high = measured[key], band_values["low"], band_values["high"]
        # A margin outside the brand's own spread, so ordinary variation is quiet.
        margin = max((high - low) * 0.5, band_values["median"] * 0.25, 0.3)
        label = key.replace("_per_100_words", "").replace("_", " ")
        if key.endswith("_per_100_words") and not _enough_evidence(measured, key, actual, low, high):
            continue  # two occurrences in ninety words is variance, not a voice
        if actual < low - margin:
            notes.append(f"{label}: brand {low:g}–{high:g}, this draft {actual:g}. {below}")
        elif actual > high + margin:
            notes.append(f"{label}: brand {low:g}–{high:g}, this draft {actual:g}. {above}")
    return notes


# Headings a brief, treatment or strategy deck uses to organise itself. A piece
# of published writing does not carry a section called CHARACTERS or KEY
# MESSAGES; a document planning one does.
# Matched word by word, because these headings are rarely bare: the treatment
# that prompted this carried "LOGLINE (draft - to be refined as more of the
# story is added)" and "CHARACTER PROFILES", neither of which equals anything a
# fixed list would hold.
_BRIEF_HEADINGS = frozenset({
    "logline", "synopsis", "premise", "treatment", "brief", "character",
    "characters", "objective", "objectives", "audience", "messages",
    "messaging", "deliverables", "themes", "background", "summary",
    "overview", "assumptions", "scope", "beats", "outline", "continuation",
    "genre", "positioning", "rationale", "requirements", "specification",
})

# The register of a document describing writing rather than being it.
_PLANNING_PHRASES = (
    "may be developed", "will be developed", "function more as", "at this stage",
    "the goal is", "we want", "the aim is", "should feel", "should read",
    "needs to", "the reader should", "the viewer should", "to be decided",
    "tbd", "placeholder", "for now", "further developed", "in future episodes",
)


def looks_like_a_brief(text: str) -> bool:
    """Whether a document reads as planning for writing rather than writing.

    Tagged as brand voice, a treatment teaches the Brain the wrong thing twice
    over: its section titles become "brand assets" the writer is told it may
    use, and its explanatory register sets the targets the writer aims at. Four
    screenplays and one treatment produced a script ordered to reuse another
    story's act titles.

    Advisory only. Plenty of brands publish documents that look like this, and
    only the person uploading knows which they have — so this warns and the
    upload proceeds.
    """
    if not text or len(text) < 200:
        return False
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False

    def is_brief_heading(line: str) -> bool:
        if not _is_heading(line):
            return False
        head = re.split(r"[(—–-]", line.strip(" #*:-").lower())[0]
        return any(word in _BRIEF_HEADINGS for word in re.findall(r"[a-z]+", head))

    headings = sum(1 for line in lines if is_brief_heading(line))
    lowered = text.lower()
    phrases = sum(1 for phrase in _PLANNING_PHRASES if phrase in lowered)
    bullets = sum(1 for line in lines if line[:1] in "-*•" or re.match(r"\d+[.)]\s", line))

    # Two independent signals, so a published piece with one list or one
    # "Summary" heading is not second-guessed.
    signals = (headings >= 2) + (phrases >= 3) + (bullets > len(lines) * 0.4)
    return signals >= 2
