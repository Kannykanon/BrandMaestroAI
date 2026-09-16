"""Facts from a product document, kept in the source's own words.

The case these were written from: a treatment said "He finds a group of more
than seven men". The copying gate called that nine copied words and told the
writer to keep the fact and discard the wording. The next draft said "He
observes a congregation. Numerous individuals are present. They exceed seven."
Nothing in the system objected — the claim was not fabricated, not unattributed
and no longer copied. It was just gone.
"""
from utils.fact_spans import extract_fact_spans, missing_fact_spans, render_fact_spans

TREATMENT = """KANCITY — STORY TREATMENT

NOTE: version 3 of this treatment, superseding the two earlier drafts.

Kan is a student. He loses his wallet outside the lecture hall.

After lectures, he calls the number. A voice tells him to come outside the school premises.
He finds a group of more than seven men, seated, drinking. They wave him over.

The debt stands at 40,000 naira and must be cleared within 14 days.
Kan has been in the city almost three years.
"""


class TestWhatCountsAsAFact:
    def test_a_quantity_keeps_its_qualifier(self):
        spans = extract_fact_spans(TREATMENT)
        assert "more than seven men" in spans, (
            "'seven men' and 'more than seven men' are different claims"
        )

    def test_money_and_deadlines_are_facts(self):
        spans = " | ".join(extract_fact_spans(TREATMENT))
        assert "40,000 naira" in spans and "14 days" in spans

    def test_a_hedged_duration_keeps_the_hedge(self):
        assert "almost three years" in extract_fact_spans(TREATMENT)

    def test_sequence_words_are_not_facts(self):
        """'The first time he calls' is a sequence, not a count. Extracting it
        fills the writer's must-keep list with words that carry nothing."""
        spans = extract_fact_spans("The first time he calls, nobody answers. The second call connects.")
        assert spans == []

    def test_one_fact_stated_twice_is_one_span(self):
        twice = TREATMENT + "\nA loose ensemble of more than seven men encountered at the meeting point."
        assert [s for s in extract_fact_spans(twice) if "seven" in s] == ["more than seven men"]

    def test_the_documents_own_version_note_is_not_a_product_fact(self):
        spans = " | ".join(extract_fact_spans(TREATMENT))
        assert "version 3" not in spans and "two earlier drafts" not in spans

    def test_nothing_to_extract_is_not_an_error(self):
        assert extract_fact_spans("") == [] and extract_fact_spans("He waits. Nobody comes.") == []


class TestWhatThePromptSays:
    def test_the_block_says_these_are_the_only_words_to_take(self):
        block = render_fact_spans(["more than seven men"])
        assert '"more than seven men"' in block
        assert "only words you may take" in block
        assert "Build your own sentences around them" in block

    def test_no_facts_means_no_block(self):
        assert render_fact_spans([]) == ""


class TestLosingAFact:
    SPANS = ["more than seven men", "40,000 naira"]

    def test_the_draft_that_softened_a_count_is_caught(self):
        draft = "He observes a congregation. Numerous individuals are present. They exceed seven."
        assert missing_fact_spans(draft, self.SPANS) == ["more than seven men"], (
            "the failure this module exists for went unnoticed by every other gate"
        )

    def test_the_hand_written_correct_version_passes(self):
        gold = "He calls after lectures.\n\nMore than seven men. Seated. Drinking.\n\nHe sits."
        assert missing_fact_spans(gold, self.SPANS) == [], (
            "the gold retelling keeps the fact and throws the source's sentence away"
        )

    def test_a_piece_that_never_covers_the_scene_is_not_failed_for_it(self):
        """Not every piece covers every part of its source. Scope is an
        editorial choice; stating a fact wrongly is not."""
        trailer = "A city. A student. A debt he cannot pay."
        assert missing_fact_spans(trailer, self.SPANS) == []

    def test_a_dropped_qualifier_is_a_dropped_fact(self):
        assert missing_fact_spans("He finds seven men waiting.", ["more than seven men"])

    def test_writing_a_number_out_in_words_is_not_a_lost_fact(self):
        """A blog whose research said "about 40% of marketers" and which wrote
        "four in ten marketers" has not lost anything. Failing that is the
        tight-rule behaviour this whole change exists to undo — so a fact is
        only reported when the draft states the number and states it wrongly."""
        assert missing_fact_spans("Four in ten marketers say so.", ["about 40% of marketers"]) == []

    def test_case_and_punctuation_do_not_matter(self):
        assert missing_fact_spans("MORE THAN SEVEN MEN — seated.", ["more than seven men"]) == []
