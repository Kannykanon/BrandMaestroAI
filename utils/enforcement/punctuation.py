"""Punctuation rules, read from each brand's own measured habits.

Nothing here is hardcoded to a brand: which marks are banned comes from that
brand's PUNCTUATION HABITS section, and the prohibition is scoped to the mark
it actually refers to.
"""
import logging
import re

logger = logging.getLogger(__name__)


# Cues that a punctuation mark is prohibited, and cues that it is actively
# used. Ordered longest-first so "not used" is matched before "used".
BAN_CUES = (
    "consistently absent", "are absent", "is absent", "absent",
    "not used", "never used", "never", "avoided", "avoid", "avoids",
    "omitted", "excluded", "prohibited", "banned", "no use of",
    "rarely", "rare",
)
USE_CUES = (
    "heavy and consistent use", "heavy use", "heavily", "consistent use",
    "frequently", "frequent", "commonly", "common", "default",
    "dominant", "primarily", "occasionally", "occasional",
    "are used", "is used", "uses", "employs", "relies on",
)


def mark_is_banned(rules_text: str, aliases: tuple) -> bool:
    """Decide whether ONE punctuation mark is prohibited by this brand.

    Previously both callers used a document-level test: if the PUNCTUATION
    HABITS block contained "avoid"/"absent" ANYWHERE, then every mark merely
    NAMED anywhere in that block was treated as banned. That silently inverts
    brands whose rules mix prohibition and prescription in one section. A real
    extracted example:

        "Heavy and consistent use of exclamation marks for emphasis ...
         Periods are rare, and semicolons, ellipses, and em dashes are
         consistently absent."

    The word "absent" (about semicolons) flipped the gate, "exclamation"
    appeared in the block, and every "!" was rewritten to "." — deleting the
    single most distinctive mark of that brand's voice, on every iteration.

    Instead, for each mention of the mark, compare the nearest ban cue and the
    nearest use cue within the same sentence and let the closer one decide. A
    mark described as used anywhere is never stripped: wrongly leaving a mark
    in costs a line of enforcer feedback, while wrongly removing one destroys
    the brand's voice with no way for the writer to recover it.
    """
    text = (rules_text or "").lower()
    if not text:
        return False

    verdicts = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for alias in aliases:
            for hit in re.finditer(re.escape(alias), sentence):
                pos = hit.start()

                def nearest(cues):
                    best = None
                    for cue in cues:
                        for c in re.finditer(re.escape(cue), sentence):
                            d = abs(c.start() - pos)
                            if best is None or d < best:
                                best = d
                    return best

                ban_d, use_d = nearest(BAN_CUES), nearest(USE_CUES)
                if ban_d is None and use_d is None:
                    continue
                if use_d is None:
                    verdicts.append("ban")
                elif ban_d is None:
                    verdicts.append("use")
                else:
                    verdicts.append("ban" if ban_d <= use_d else "use")

    if not verdicts:
        return False
    # Any evidence the brand actually uses the mark wins.
    return "use" not in verdicts and "ban" in verdicts


def sanitize_banned_punctuation(content: str, metrics: str) -> tuple[str, list[str]]:
    """
    Deterministically strip/replace simple banned punctuation marks instead of
    asking the Writer to notice and fix them through another revision round.

    A revision pass fixes the ONE excerpt it was shown while free to introduce
    a fresh violation elsewhere in the same rewrite — observed in practice as
    an em-dash preflight failure recurring for 3 straight iterations even with
    the exact offending text quoted each time. A simple character-level rule
    like "no em-dashes" is unambiguous and mechanical, so it deserves a
    mechanical fix with a guaranteed outcome rather than a probabilistic one.

    Which marks are banned is still read dynamically from THIS brand's own
    extracted PUNCTUATION HABITS (same section run_preflight_checks reads) —
    nothing here is hardcoded to any specific brand's rules or vocabulary.

    Only marks whose removal reliably keeps a sentence grammatical get a full
    rewrite here (exclamation, em-dash, semicolon, ellipsis). A banned
    question mark gets a blunter fallback: swap "?" for "." rather than
    rephrase into a true statement. That can occasionally read a little flat
    grammatically, but it guarantees the loop ends — observed in practice on
    an "ad" content type, where 3 straight revision iterations rewrote around
    one flagged question and reintroduced a different one each time, ending
    in a hard rejection the brand's own content would never have earned on
    substance. A guaranteed-compliant, slightly flat sentence beats a
    guaranteed rejection.

    Returns (sanitized content, list of human-readable fixes applied).
    """
    fixed = content
    applied = []

    punctuation_match = re.search(
        r"PUNCTUATION HABITS:\n(.*?)(?=\n[A-Z_]+:|\n#|\Z)", metrics, re.DOTALL | re.IGNORECASE
    )
    if punctuation_match:
        rules = punctuation_match.group(1)
        if mark_is_banned(rules, ("exclamation",)) and "!" in fixed:
            fixed = fixed.replace("!", ".")
            applied.append("exclamation marks -> periods")

        if mark_is_banned(rules, ("em dash", "em-dash", "emdash")) and ("—" in fixed or "--" in fixed):
            # A dash trailing off at the end of a sentence/line has nothing to
            # join with a comma — end the sentence instead of leaving one dangling.
            fixed = re.sub(r"\s*—\s*(?=[\"'”]?\s*(?:\n|$))", ".", fixed)
            fixed = re.sub(r"\s*--\s*(?=[\"'”]?\s*(?:\n|$))", ".", fixed)
            # Any dash still remaining is a mid-sentence aside — join with a comma.
            fixed = re.sub(r"\s*—\s*", ", ", fixed)
            fixed = re.sub(r"\s*--\s*", ", ", fixed)
            applied.append("em-dashes -> commas")

        if mark_is_banned(rules, ("semicolon",)) and ";" in fixed:
            # "X; y..." (independent clauses) -> "X. Y..." (two sentences)
            fixed = re.sub(r";\s*([a-z])", lambda m: ". " + m.group(1).upper(), fixed)
            fixed = fixed.replace(";", ".")  # anything left (e.g. "; However") is already capitalized
            applied.append("semicolons -> sentence breaks")

        if mark_is_banned(rules, ("ellipsis", "ellipses")) and ("..." in fixed or "…" in fixed):
            fixed = fixed.replace("…", "...")  # normalize to one form before splitting
            fixed = re.sub(r"\.\.\.\s*([a-z])", lambda m: ". " + m.group(1).upper(), fixed)
            fixed = re.sub(r"\.{2,}", ".", fixed)  # any remaining run not followed by a lowercase letter
            applied.append("ellipses -> periods")

    question_match = re.search(
        r"QUESTION USAGE:\n(.*?)(?=\n[A-Z_]+:|\n#|\Z)", metrics, re.DOTALL | re.IGNORECASE
    )
    if question_match:
        q_rules = question_match.group(1).lower()
        if ("avoid" in q_rules or "absent" in q_rules or "not use" in q_rules or "none" in q_rules) and "?" in fixed:
            fixed = fixed.replace("?", ".")
            applied.append("question marks -> periods (blunt fallback, not a rephrase)")

    if fixed != content:
        # Tidy up artifacts the substitutions above can leave behind: a
        # dangling comma right before sentence-ending punctuation, doubled
        # punctuation, or doubled spacing.
        fixed = re.sub(r",\s*([.,])", r"\1", fixed)
        fixed = re.sub(r"\.\s*\.", ".", fixed)
        fixed = re.sub(r"[ \t]{2,}", " ", fixed)
        fixed = re.sub(r"[ \t]+([.,])", r"\1", fixed)

    return fixed, applied
