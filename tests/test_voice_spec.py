"""The voice spec: how a brand writes, measured from its documents and never quoting them.

The cases come from a real generation. Four screenplay retellings were uploaded
as brand voice; the Brain measured them whole, including each document's notes
and its closing analysis, and set a target of 6.3 four-plus-syllable words per
100 — a rate made by "protagonist", "original" and "identity". The draft chased
that number and reached it with "congregation", "numerical contact" and "He must
convene beyond campus boundaries", then passed.
"""
from utils.brand_profile import extract_permitted_claims, filter_asset_bank, is_brand_asset
from utils.enforcement.constants import MAX_VERBATIM_SPAN_WORDS, NARRATIVE_VERBATIM_SPAN_WORDS
from utils.enforcement.provenance import find_extractive_spans
from utils.voice_spec import (
    beat_grammar,
    corpus_spec,
    longest_shared_run,
    prose_only,
    render_spec,
    shape_metrics,
    voice_diagnostics,
)

SCREENPLAY = """JOHN WICK (2014)
DETAILED SCREENPLAY-STYLE RETELLING
ORIGINAL RETELLING — NOT THE ORIGINAL SCREENPLAY

NOTE: This is an original retelling. It does not reproduce the copyrighted screenplay.

========================================
ACT I — WHAT'S LEFT
========================================

FADE IN:

INT. WICK HOME — DAY

John sits alone with his grief.

His wife has recently died.

A package arrives, sent by her before she passed.

Inside is a dog. There is a note. He reads it once and puts it down.

He does not cry. He feeds the dog.

Morning. He drives to the station and fills the tank.

Three men watch him from the far pump. One of them asks about the car.

JOHN
It's not for sale.

They take it that night. They kill the dog.

He goes to the basement. He breaks the floor open with a hammer.

Under it are guns, and coins, and the life he agreed to leave.

FADE OUT.

========================================
STRUCTURAL LESSON
========================================

The retelling demonstrates a fundamental characteristic of the revenge construction: an ordinary
life, interrupted by an extraordinary organizational investigation, generates an acceleration of
consequences that the protagonist's original identity had previously suppressed. The escalation
communicates the accumulated obligations of the underworld institution.
"""

PLAIN_BRAND = """# MEASURED MECHANICS
- nominalisations_per_100_words: 0.9
- nominalisations_per_100_words_low: 0.7
- nominalisations_per_100_words_high: 1.2
- four_plus_syllable_words_per_100_words: 3.3
- contractions_per_100_words: 4.0
- median_words_per_sentence: 7.0
- median_words_per_sentence_low: 6.0
- median_words_per_sentence_high: 8.0
"""


class TestProse:
    def test_framing_headings_and_appendix_are_not_the_brands_prose(self):
        prose = prose_only(SCREENPLAY)
        assert "John sits alone with his grief." in prose
        assert "NOTE:" not in prose, "the document's own disclaimer is not its voice"
        assert "ACT I" not in prose and "FADE IN" not in prose and "INT. WICK HOME" not in prose
        assert "intelligence organization investigation" not in prose, (
            "the closing analysis is what set a plain corpus's long-word rate"
        )

    def test_the_appendix_is_what_inflated_the_long_word_rate(self):
        whole = shape_metrics(SCREENPLAY)["four_plus_syllable_words_per_100_words"]
        prose = shape_metrics(prose_only(SCREENPLAY))["four_plus_syllable_words_per_100_words"]
        assert prose < whole, "measuring the whole document counts words the brand never wrote in scenes"


class TestSpec:
    def test_bands_come_from_the_documents_not_one_number(self):
        plain = SCREENPLAY
        ornate = SCREENPLAY.replace("John sits alone with his grief.",
                                    "John contemplates the ramifications of his bereavement situation.")
        spec = corpus_spec([plain, ornate])
        band = spec["bands"]["nominalisations_per_100_words"]
        assert band["documents"] == 2 and band["low"] <= band["median"] <= band["high"]

    def test_rendered_spec_is_rules_and_shapes_never_the_brands_sentences(self):
        documents = [SCREENPLAY]
        spec = render_spec(corpus_spec(documents), beat_grammar(documents))
        assert "Sentences are short" in spec and "HOW ITS OPENINGS TEND TO GO" in spec
        assert "transition line" in spec and "scene heading" in spec
        assert longest_shared_run(spec, SCREENPLAY) < 8, "the spec quoted the brand's own writing back"
        assert "John" not in spec, "the corpus's content leaked into the spec"

    def test_beat_grammar_describes_shapes(self):
        grammar = beat_grammar([SCREENPLAY])
        assert "transition line" in grammar["opening"][0]
        assert any("sentence" in shape for shape in grammar["opening"])

    def test_the_habits_carry_no_rates_for_a_judge_to_cite(self):
        """Every rule used to quote the number it came from, and the voice pass
        faulted drafts against whichever one they had crossed — three rounds
        ordering sentences joined, then one ordering them split."""
        import re

        spec = render_spec(corpus_spec([SCREENPLAY]), beat_grammar([SCREENPLAY]))
        habits = spec.split("SENTENCE AND PARAGRAPH HABITS:")[1].split("HOW ITS")[0]
        assert not re.search(r"\d", habits), f"a rate survived into the habits: {habits}"

    def test_layout_is_not_reported_as_voice(self):
        """Screenplays are laid out one action per line, so this measures 99 in
        a plain corpus. Reported as a rule it produced an instruction to
        reformat an entire script."""
        spec = render_spec(corpus_spec([SCREENPLAY]), beat_grammar([SCREENPLAY]))
        assert "paragraphs are a single sentence" not in spec

    def test_shapes_carry_no_word_count_to_be_matched(self):
        """Printed as "(3 words)" these read as a specification, and the
        enforcer twice refused a draft for not matching "the brand's specific
        sentence length sequence"."""
        grammar = beat_grammar([SCREENPLAY])
        assert not any("words)" in shape for shape in grammar["opening"] + grammar["closing"])


class TestDiagnostics:
    CONSULTANCY = ("Our approach meticulously examines the presentation and depicts its distractions as a "
                   "diagnostic failure to prioritise the integrity of effort over immediate gratification. "
                   "This calculated persistence challenges the pervasive assumption that velocity alone "
                   "equates to success. It demonstrates a superior understanding of the situation and "
                   "culminates in a lasting humiliation of every competitor in the organisation. ") * 2

    def test_drift_is_described_in_words(self):
        notes = voice_diagnostics(self.CONSULTANCY, PLAIN_BRAND)
        joined = " ".join(notes)
        assert "abstract nouns" in joined and "naming actions instead of performing them" in joined
        assert "brand 0.7–1.2" in joined, "the note gives the brand's range"
        assert "Bring it to" not in joined and "roughly" not in joined, (
            "a number to hit is what produced 'convene' and 'congregation'"
        )

    def test_a_couple_of_long_words_in_a_short_draft_say_nothing(self):
        short = ("He waits. Nobody comes. The road is closing. Not because anyone decided it, but because "
                 "the ice will not hold. His registration expired. He sleeps in the truck.")
        assert not any("abstract" in n for n in voice_diagnostics(short, PLAIN_BRAND))

    def test_a_draft_chopped_to_fragments_is_described_too(self):
        chopped = "He sits. He waits. He stands. He leaves. He returns. He sits. He waits. He stands. " * 4
        notes = " ".join(voice_diagnostics(chopped, PLAIN_BRAND))
        assert "Sentences are shorter" in notes


class TestAssetBank:
    BANK = """# BRAND ASSET BANK

NAMED FRAMEWORKS & METHODOLOGIES:
- STRUCTURAL LESSON (appears in 2/3 profiles)
- ACT I — A LIFE ALMOST REBUILT (appears in 1/3 profiles)
- THE TRUST LADDER (appears in 3/3 profiles)

SOCIAL PROOF CLAIMS:
- 40 retail clients (appears in 3/3 profiles)
"""

    def test_one_documents_act_titles_are_not_brand_assets(self):
        assert not is_brand_asset("- ACT I — A LIFE ALMOST REBUILT (appears in 1/3 profiles)")
        assert not is_brand_asset("- STRUCTURAL LESSON (appears in 2/3 profiles)")
        assert is_brand_asset("- THE TRUST LADDER (appears in 3/3 profiles)")
        assert is_brand_asset("- 40 retail clients (appears in 3/3 profiles)")

    def test_filtering_keeps_the_brands_facts_and_drops_one_storys_titles(self):
        filtered = filter_asset_bank(self.BANK)
        assert "THE TRUST LADDER" in filtered and "40 retail clients" in filtered
        assert "A LIFE ALMOST REBUILT" not in filtered, (
            "a script was ordered to reuse another story's act titles as permitted claims"
        )

    def test_narrative_content_has_no_closed_list_of_claims(self):
        narrative = extract_permitted_claims(self.BANK, "script")
        assert "no closed list" in narrative and "act names" in narrative
        marketing = extract_permitted_claims(self.BANK, "blog")
        assert "COMPLETE list" in marketing and "THE TRUST LADDER" in marketing


class TestCopyingScope:
    TREATMENT = ("Kan loses his wallet. After lectures, he calls the number. A voice tells him to come outside "
                 "the school premises. He finds a group of more than seven men. They tell him to drink with them. "
                 "He says no.")
    RETELLING = ("Kan calls the number after lectures. A voice tells him to come outside the school premises. "
                 "He finds more than seven men. They tell him to drink. He says no.")

    def test_a_correct_retelling_keeps_the_facts_without_being_called_copying(self):
        assert find_extractive_spans(self.RETELLING, self.TREATMENT,
                                     max_span=NARRATIVE_VERBATIM_SPAN_WORDS) == [], (
            "the hand-written correct retelling was flagged for keeping its facts"
        )

    def test_wholesale_lifting_is_still_caught(self):
        lifted = self.TREATMENT + " " + self.TREATMENT
        assert find_extractive_spans(lifted, self.TREATMENT, max_span=NARRATIVE_VERBATIM_SPAN_WORDS)

    def test_marketing_content_keeps_the_strict_threshold(self):
        assert NARRATIVE_VERBATIM_SPAN_WORDS > MAX_VERBATIM_SPAN_WORDS
        assert find_extractive_spans(self.RETELLING, self.TREATMENT, max_span=MAX_VERBATIM_SPAN_WORDS)
