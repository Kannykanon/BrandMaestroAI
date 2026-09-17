"""What the voice pass is allowed to ask for, and what the Brain may hand it.

Four failures from one generation log, all of them the system arguing with
itself rather than with the draft:

  04:19  "too choppy" — five passages rewritten into longer sentences
  04:21  the writer complied
  04:21  the same pass took the sentence it had asked for and split it in three

  twice: "the opening pattern fails to match the brand's specific sentence
  length sequence" — against a spec that was a description, printed as a
  numbered list with word counts

  on a screenplay: "Replace all fabricated claims with permitted alternatives
  from the brand asset bank. Use only exact client counts, percentages, and
  framework names."

  "incorporate signature constructions like the 'He realizes that...'
  declarative phrase" — a sentence from one of the uploaded screenplays,
  offered to the writer as a move to reuse
"""
import glob
import os

import pytest

from utils.brand_profile import extract_permitted_claims, strip_quoted_constructions
from utils.voice_spec import beat_grammar, corpus_spec, longest_shared_run, render_spec, voice_directions

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kancity")

BRAND = """# MEASURED MECHANICS
- median_words_per_sentence: 7.0
- median_words_per_sentence_low: 6.75
- median_words_per_sentence_high: 7.62
- share_sentences_under_6_words: 33.0
- share_sentences_under_6_words_low: 30.35
- share_sentences_under_6_words_high: 36.3
- nominalisations_per_100_words: 2.7
- nominalisations_per_100_words_low: 2.11
- nominalisations_per_100_words_high: 3.1
"""

CHOPPED = "He sits. He waits. He stands. He leaves. He returns. He looks. He listens. He goes. " * 5

# Measured at a median of 7 words a sentence with a third of them under six —
# both inside this brand's own range, which is what makes them settled.
IN_RANGE = (
    "EXT. MEETING POINT - 5 P.M.\n"
    "He calls the number after lectures.\n"
    "A voice answers and tells him to wait.\n"
    "He waits.\n"
    "More than seven men sit at the table.\n"
    "They wave him over.\n"
    "He takes the seat they offer him.\n"
    "They tell him to drink with them.\n"
    "He says no.\n"
    "The mood turns before he finishes saying it.\n"
)


@pytest.fixture(scope="module")
def voice_corpus():
    paths = glob.glob(os.path.join(FIXTURES, "voice", "*.txt"))
    return "\n".join(open(p, encoding="utf-8").read() for p in paths)


class TestTheJudgeCannotContradictItself:
    def test_a_measurement_inside_the_brands_range_is_settled(self):
        directions = voice_directions(IN_RANGE, BRAND)
        settled = [d for d in directions if "SETTLED" in d]
        assert len(settled) >= 2, (
            "sentence length and how often sentences end early are both in range here; "
            "leaving them arguable is what let one round undo the last one"
        )
        assert "do not raise it as a fault" in " ".join(settled)

    def test_a_draft_that_is_out_of_range_may_only_be_moved_toward_it(self):
        direction = next(d for d in voice_directions(CHOPPED, BRAND) if "end early" in d)
        assert "ABOVE" in direction
        assert "never ask for changes that raise it further" in direction

    def test_the_directions_are_limits_not_instructions(self):
        """Stated as instructions they would damage the right answer: the
        hand-written correct script measures below the corpus on sentence
        length, word length and abstraction, all three, on purpose."""
        first = voice_directions(CHOPPED, BRAND)[0]
        assert "not instructions to change anything" in first
        assert "limits on what you are allowed to ask for" in first

    def test_being_plainer_than_the_brand_is_not_automatically_a_fault(self):
        plain = ("He waits. Nobody comes. The road is closing, and not because anyone decided it. "
                 "The ice will not hold. He sleeps in the truck. " * 3)
        below = [d for d in voice_directions(plain, BRAND) if "BELOW" in d]
        assert below, "this draft is below the brand on sentence length"
        assert "not a fault by itself" in " ".join(below)

    def test_nothing_to_measure_says_nothing(self):
        assert voice_directions("Too short to measure.", BRAND) == []
        assert voice_directions(IN_RANGE, "") == []


class TestTheSpecIsNotATemplate:
    def test_openings_are_described_not_numbered(self, voice_corpus):
        documents = [open(p, encoding="utf-8").read()
                     for p in glob.glob(os.path.join(FIXTURES, "voice", "*.txt"))]
        spec = render_spec(corpus_spec(documents), beat_grammar(documents))
        assert "HOW ITS OPENINGS TEND TO GO:" in spec
        assert "OPENING IS BUILT AS:" not in spec, "a numbered list reads as a form to fill in"
        assert "1." not in spec.split("HOW ITS OPENINGS")[1][:200]

    def test_shapes_carry_no_word_counts_to_match(self, voice_corpus):
        documents = [open(p, encoding="utf-8").read()
                     for p in glob.glob(os.path.join(FIXTURES, "voice", "*.txt"))]
        spec = render_spec(corpus_spec(documents), beat_grammar(documents))
        opening = spec.split("HOW ITS OPENINGS TEND TO GO:")[1].split("\n")[0]
        assert "words)" not in opening, (
            "the enforcer refused a draft for not matching a sentence length sequence"
        )
        assert "short sentence" in opening or "medium-length sentence" in opening

    def test_the_spec_says_outright_that_it_is_not_a_template(self, voice_corpus):
        documents = [open(p, encoding="utf-8").read()
                     for p in glob.glob(os.path.join(FIXTURES, "voice", "*.txt"))]
        spec = render_spec(corpus_spec(documents), beat_grammar(documents))
        assert "Nothing here is a template" in spec
        assert "never count words to match a shape" in spec

    def test_it_still_never_quotes_the_corpus(self, voice_corpus):
        documents = [open(p, encoding="utf-8").read()
                     for p in glob.glob(os.path.join(FIXTURES, "voice", "*.txt"))]
        spec = render_spec(corpus_spec(documents), beat_grammar(documents))
        assert longest_shared_run(spec, voice_corpus) < 8


class TestAStoryIsNotJudgedAgainstAnAssetBank:
    def test_narrative_permissions_say_there_is_no_list(self):
        claims = extract_permitted_claims("# BRAND ASSET BANK\n- 40 clients (appears in 3/3 profiles)\n", "script")
        assert "no closed list" in claims
        for marketing in ("client counts", "percentages", "COMPLETE list"):
            assert marketing not in claims

    def test_marketing_still_gets_its_closed_list(self):
        claims = extract_permitted_claims("# BRAND ASSET BANK\n- 40 clients (appears in 3/3 profiles)\n", "blog")
        assert "COMPLETE list" in claims and "40 clients" in claims


class TestTheBrainDescribesMovesAndNeverQuotesThem:
    BRAIN = """# SIGNATURE CONSTRUCTIONS
- The 'He realizes that...' declarative phrase to introduce pivotal plot points
- States the consequence before the cause, so the reader meets the result first
- A one-line paragraph that withholds the subject until the next line

# BRAND ASSET BANK
- 40 retail clients (appears in 3/3 profiles)
"""

    def test_a_construction_written_as_a_quotation_is_removed(self, voice_corpus):
        kept = strip_quoted_constructions(self.BRAIN, voice_corpus)
        assert "He realizes that" not in kept, (
            "a sentence from one screenplay was offered to the writer as a move to reuse"
        )

    def test_the_real_descriptions_survive(self, voice_corpus):
        kept = strip_quoted_constructions(self.BRAIN, voice_corpus)
        assert "States the consequence before the cause" in kept
        assert "withholds the subject until the next line" in kept

    def test_other_sections_are_left_alone(self, voice_corpus):
        """The asset bank is grounded per claim and is meant to be verbatim —
        it is the one part of the Brain that carries facts."""
        assert "40 retail clients" in strip_quoted_constructions(self.BRAIN, voice_corpus)

    def test_quote_marks_no_longer_hide_the_overlap(self, voice_corpus):
        """The tokeniser kept "'He" as one word, so a phrase quoted straight out
        of the corpus measured as a two-word overlap and passed."""
        assert longest_shared_run("the 'He realizes that...' phrase", voice_corpus) >= 3

    def test_no_corpus_means_no_filtering(self):
        assert strip_quoted_constructions(self.BRAIN, "") == self.BRAIN


class TestTheJudgeIsGivenCraftNotRates:
    """The run that forced this: three rounds telling a script to join its
    sentences, then one telling it to put every sentence on its own line —
    each defensible against a different statistic the judge had been handed."""

    def _brief(self, **fields):
        from prompts.enforcer import ENFORCER_PROMPT

        defaults = dict(
            metrics="", content="", topic="", research="", permitted_claims="",
            human_directive="", voice_spec="SPEC", voice_craft="CRAFT",
            closed_subjects="- sentence rhythm",
        )
        defaults.update(fields)
        return ENFORCER_PROMPT.format(**defaults)

    def test_the_brief_asks_one_question(self):
        brief = self._brief()
        assert "would a reader who knows this brand believe it wrote this" in brief

    def test_it_is_forbidden_to_reason_from_numbers(self):
        brief = self._brief()
        assert "do not count words, clauses, syllables or paragraphs" in brief
        assert "If your only complaint can be expressed as a" in brief
        assert "there is no complaint" in brief
        assert "not cite a rate or a range as the reason for anything" in brief

    def test_layout_is_named_as_not_voice(self):
        """The final round of that run ordered a whole script reformatted."""
        assert "That is format, not voice" in self._brief()

    def test_being_plainer_than_the_brand_is_named_as_not_a_fault(self):
        """The hand-written correct script is plainer than its own corpus."""
        assert "not wrong for being lean" in self._brief()

    def test_closed_subjects_reach_the_judge(self):
        brief = self._brief(closed_subjects="- sentence rhythm")
        assert "CLOSED, DO NOT RAISE AGAIN" in brief and "sentence rhythm" in brief

    def test_the_craft_section_is_where_the_brand_is_described(self):
        assert "THE MOVES THIS BRAND MAKES" in self._brief()

    def test_the_old_numeric_channels_are_gone(self):
        from prompts.enforcer import ENFORCER_PROMPT

        for placeholder in ("{voice_notes}", "{voice_directions}", "{previous_voice_feedback}"):
            assert placeholder not in ENFORCER_PROMPT
