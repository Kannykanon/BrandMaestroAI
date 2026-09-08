"""Tests for the provenance gates.

Every case here is a failure that actually reached a generation, or a legitimate
piece of copy that one of these gates wrongly rejected. Both directions matter
equally: a gate that flags correct copy burns the revision budget and the
generation ends at MAX_ITERATIONS scoring zero, which is indistinguishable from
the pipeline being broken.
"""
import pytest

from utils.enforcement import (
    find_altered_quotations,
    find_extractive_spans,
    find_fabricated_dialogue,
    find_unbranded_emphasis_caps,
)


# A miniature two-document corpus with the shapes that caused trouble: a
# quotation split around its attribution, boilerplate repeated across documents,
# an award and festival name that cannot be paraphrased, and script dialogue.
SOURCE = """
FOR IMMEDIATE RELEASE

Harbor Line Pictures Takes Worldwide Rights to SALVAGE

The production built its tank at a former shipyard in Ardrossan and dressed a
wreck interior inside it.

"People assume underwater sound is muffled," said Okpara. "It isn't. It's loud,
and it's close, and it's mostly your own body. That was the note for seven
months."

"I've never made anything for a room before," said Kowalczyk.

SALVAGE won the Grand Jury Prize for Direction at the Cascadia International
Film Festival.

About Harbor Line Pictures

Harbor Line Pictures acquires and distributes narrative features in North
America, the United Kingdom and Ireland. The company releases between six and
nine films a year.

###

FOR IMMEDIATE RELEASE

SALVAGE Sets 6 February Release

DIALOGUE SELECTS

WALE: Sixteen days. Then the weather turns and we're done whether we're finished or not.
RENATA: There's something in the hold.
SAM (over comms, breathing hard): Wale. Wale, she's not coming up.

About Harbor Line Pictures

Harbor Line Pictures acquires and distributes narrative features in North
America, the United Kingdom and Ireland. The company releases between six and
nine films a year.

###
"""


class TestAlteredQuotations:
    """A quotation is a claim about what somebody said. Restyling it is a
    factual error about a real person, so it must never ship."""

    @pytest.mark.parametrize("label,content", [
        (
            # The failure that prompted this gate: social copy restyled a real
            # quote into the brand's voice, inside quotation marks.
            "wholesale de-contraction",
            'Okpara states: "People assume underwater sound is muffled. It is not. '
            'It is loud. It is close. It is mostly your own body."',
        ),
        (
            "tense changed",
            'Kowalczyk said: "I have never made anything for a room before."',
        ),
        (
            # Only the first sentence altered — averaged away unless the check
            # descends to sentence level.
            "one sentence altered, remainder exact",
            'Okpara said: "People assume underwater sound is muffled. It is not. '
            "It's loud, and it's close, and it's mostly your own body.\"",
        ),
        (
            "script dialogue de-contracted",
            "RENATA: There is something in the hold.",
        ),
    ])
    def test_alterations_are_flagged(self, label, content):
        findings = find_altered_quotations(content, SOURCE)
        assert findings, f"missed an altered quotation: {label}"
        # The finding has to carry the correct wording, or the writer is being
        # told something is wrong without being told what it should say.
        assert findings[0]["source"].strip()

    @pytest.mark.parametrize("label,content", [
        (
            # The source splits this around "said Okpara"; reproducing it whole
            # without the attribution is correct, not a merge of two statements.
            "exact reproduction across a split quotation",
            'Okpara said: "People assume underwater sound is muffled. It isn\'t. '
            "It's loud, and it's close, and it's mostly your own body. That was "
            'the note for seven months."',
        ),
        (
            "shortened but exact",
            'As Okpara put it: "It isn\'t. It\'s loud, and it\'s close"',
        ),
        (
            # A curly apostrophe where the source had a straight one is
            # typesetting, not an alteration.
            "curly apostrophes",
            "Okpara said: “It isn’t. It’s loud, and it’s close, "
            "and it’s mostly your own body.”",
        ),
        (
            "punctuation moved, words identical",
            'Okpara said: "It isn\'t! It\'s loud; and it\'s close, and it\'s mostly '
            'your own body."',
        ),
        (
            # Not this gate's job: an invented quote is find_unverified_quote_
            # attributions' business, and ordinary quoted phrasing is nobody's.
            "phrasing the brand chose itself",
            'The distributor called it "a film about the price of a day\'s work at depth."',
        ),
        (
            "script dialogue reproduced exactly",
            "RENATA: There's something in the hold.\n"
            "SAM (over comms, breathing hard): Wale. Wale, she's not coming up.",
        ),
    ])
    def test_faithful_quotations_pass(self, label, content):
        assert not find_altered_quotations(content, SOURCE), \
            f"wrongly flagged a faithful quotation: {label}"

    def test_no_grounding_text_flags_nothing(self):
        assert find_altered_quotations('He said: "anything at all, at length."', "") == []


class TestExtractiveSpans:
    """The draft must be written from the research, not assembled out of it —
    without treating the brand's own fixed assets as research."""

    @pytest.mark.parametrize("label,content", [
        (
            "descriptive prose lifted verbatim",
            "The production built its tank at a former shipyard in Ardrossan and "
            "dressed a wreck interior inside it.",
        ),
    ])
    def test_copying_is_flagged(self, label, content):
        assert find_extractive_spans(content, SOURCE), f"missed copying: {label}"

    @pytest.mark.parametrize("label,content", [
        (
            # A fact. Thirteen shared words, one of them a choice: an award title
            # and a festival name cannot be paraphrased without becoming wrong.
            "award and festival names",
            "SALVAGE won the Grand Jury Prize for Direction at the Cascadia "
            "International Film Festival.",
        ),
        (
            # A quotation records what a named person said, which is a fact, and
            # a fact may be reproduced exactly.
            "a quotation, which has to match",
            'Okpara said: "It isn\'t. It\'s loud, and it\'s close, and it\'s mostly '
            'your own body."',
        ),
        (
            # Same argument: what a character says in the film is a fact about
            # the film.
            "script dialogue",
            "WALE: Sixteen days. Then the weather turns and we're done whether "
            "we're finished or not.",
        ),
    ])
    def test_facts_may_be_reproduced(self, label, content):
        assert not find_extractive_spans(content, SOURCE), \
            f"wrongly flagged a fact: {label}"

    def test_repeated_brand_phrasing_is_still_copying(self):
        """Boilerplate the brand repeats is phrasing, not a fact.

        An earlier version exempted any n-gram the source repeated, reasoning
        that an "About <company>" block closing every release is standing copy to
        be reused. That was wrong about what the uploaded documents are: they are
        references the Brand Brain learns a voice from, not product documents or
        an asset bank to paste out of. Repetition makes a phrase characteristic
        of how this brand writes, which is a reason to learn the pattern — not a
        licence to reproduce the sentence.
        """
        content = (
            "About Harbor Line Pictures\n\nHarbor Line Pictures acquires and "
            "distributes narrative features in North America, the United Kingdom "
            "and Ireland. The company releases between six and nine films a year."
        )
        assert find_extractive_spans(content, SOURCE), \
            "reproduced boilerplate was not flagged"

    def test_card_copy_is_phrasing_not_fact(self):
        """"THE HOLD IS NOT EMPTY" is writing, so a new sheet earns its own.

        Card lines were exempt on the same mistaken reasoning as the boilerplate.
        An uploaded trailer sheet shows how this brand writes a card; it is not a
        set of cards to reuse.
        """
        source = (
            "CARD COPY\n\nTHE JOB PAYS ON TONNAGE\nTHE HOLD IS NOT EMPTY\n"
            "AND THERE ARE THREE OF THEM WORKING THE SAME CONTRACT TOGETHER\n"
        )
        content = "AND THERE ARE THREE OF THEM WORKING THE SAME CONTRACT TOGETHER\n"
        assert find_extractive_spans(content, source), \
            "card copy was reproduced verbatim and not flagged"

    @pytest.mark.parametrize("label,content,should_flag", [
        # The fact is statable: an award name is short enough to fall under the
        # span threshold, so nothing stops the draft naming it.
        ("the award name alone", "GRAND JURY PRIZE FOR DIRECTION", False),
        # And statable in the draft's own framing, however long, because the
        # framing is the draft's.
        ("the award in the draft's own words",
         "WINNER OF THE GRAND JURY PRIZE FOR DIRECTION AT CASCADIA", False),
        # Two of the reference sheet's cards in sequence is the sheet's design,
        # not a fact, and that is the line.
        ("two card lines in sequence",
         "GRAND JURY PRIZE FOR DIRECTION\nCASCADIA INTERNATIONAL FILM FESTIVAL",
         True),
    ])
    def test_a_fact_is_statable_but_a_sheet_is_not_reproducible(
        self, label, content, should_flag
    ):
        """Where the line falls between a fact and the reference's phrasing."""
        source = (
            "CARD COPY\n\nGRAND JURY PRIZE FOR DIRECTION\n"
            "CASCADIA INTERNATIONAL FILM FESTIVAL\nBEST SOUND\n"
            "CASCADIA INTERNATIONAL FILM FESTIVAL\n"
        )
        assert bool(find_extractive_spans(content, source)) is should_flag, label

    def test_a_quotation_does_not_weld_two_copied_fragments_together(self):
        """A span must not extend through a quotation.

        The exemption used to require a span to sit entirely inside a quoted
        region, so a span that merely passed through one was not exempt. Because
        the quotation's own words are in the source, the extension walk crossed
        it and welded the fragments on either side into a single 62-word span,
        most of it a quotation the draft was right to reproduce. Feedback like
        that cannot be acted on.
        """
        content = (
            "Okpara worked inside the tank alongside the camera crew.\n\n"
            '"People assume underwater sound is muffled. It isn\'t. It\'s loud, and '
            "it's close, and it's mostly your own body. That was the note for "
            'seven months."\n\n'
            "The production built its tank at a former shipyard in Ardrossan and "
            "dressed a wreck interior inside it."
        )
        spans = find_extractive_spans(content, SOURCE)
        assert spans, "the genuinely copied prose should still be reported"
        assert all(span["length"] < 40 for span in spans), \
            f"a span spanned the quotation: {[s['length'] for s in spans]}"
        assert not any("muffled" in span["text"] for span in spans), \
            "the quotation was reported as copied text"

    @pytest.mark.parametrize("n_words,should_flag", [(9, True), (8, False)])
    def test_span_length_boundary_is_unchanged(self, n_words, should_flag):
        vocabulary = ("alpha beta gamma delta epsilon zeta eta theta iota kappa "
                      "lambda").split()
        source = " ".join(vocabulary)
        content = " ".join(vocabulary[:n_words])
        assert bool(find_extractive_spans(content, source)) is should_flag


class TestFabricatedDialogue:
    """Trailer copy approved at 8.1 with lines no character in the film says."""

    def test_invented_lines_are_flagged(self):
        content = (
            "WALE: How much time.\n"
            "RENATA: What is inside.\n"
            "SAM: Is she coming up.\n"
        )
        found = find_fabricated_dialogue(content, SOURCE)
        assert {f["speaker"] for f in found} == {"WALE", "RENATA", "SAM"}

    def test_real_lines_pass(self):
        content = (
            "WALE: Sixteen days. Then the weather turns and we're done whether "
            "we're finished or not.\n"
            "RENATA: There's something in the hold.\n"
            "SAM (over comms, breathing hard): Wale. Wale, she's not coming up.\n"
        )
        assert find_fabricated_dialogue(content, SOURCE) == []

    def test_label_lines_are_not_dialogue(self):
        """"RELEASE DATE: 14 APRIL" has the shape of dialogue without being it.

        Only speakers the source itself uses are checked, which is what keeps a
        trailer sheet's label lines out of this gate.
        """
        content = "RELEASE DATE: 14 APRIL.\nHOLD CONTENTS: UNSEEN.\nFILM ACCLAIM: Grand Jury Prize.\n"
        assert find_fabricated_dialogue(content, SOURCE) == []


class TestUnbrandedEmphasisCaps:
    """Social copy approved at 6.6 while shouting adjectives.

    The measured all-caps rate was 5.9 per 100 words against the brand's 4.4 —
    inside tolerance, and correctly silent. The count was right and every choice
    was wrong, so the question is which words rather than how many.
    """

    @pytest.mark.parametrize("word", [
        "EXCLUSIVE", "ACCLAIMED", "UNIQUE", "FIRST",
    ])
    def test_shouted_adjectives_are_flagged(self, word):
        content = f"It features an {word} commentary track by the director."
        assert [f["word"] for f in find_unbranded_emphasis_caps(content, SOURCE)] == [word]

    @pytest.mark.parametrize("label,content", [
        # The brand sets its own title in capitals, so the title is its
        # convention rather than the model's emphasis.
        ("the brand's own title", "SALVAGE arrives on digital and disc in April."),
        # Trailing punctuation must not make a different word of it. With "."
        # inside the character class, "SALVAGE." did not match the permitted
        # "SALVAGE" and the title was flagged every time it ended a sentence —
        # which blocked the press-release path for two whole revision rounds.
        ("the title ending a sentence", "Experience SALVAGE."),
        # Capitals on a line of their own are structural: a card, a heading, a
        # label. The measured rate governs those.
        ("a card line", "THE HOLD IS NOT EMPTY\nSALVAGE\n14 APRIL"),
        ("a speaker label", "WALE: Sixteen days."),
    ])
    def test_brand_capitalisation_passes(self, label, content):
        assert find_unbranded_emphasis_caps(content, SOURCE) == [], \
            f"wrongly flagged the brand's own capitalisation: {label}"

    def test_no_grounding_text_flags_nothing(self):
        assert find_unbranded_emphasis_caps("An EXCLUSIVE offer.", "") == []
