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

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "gold", "kancity")

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


class TestInventedWordCountsNeverReachTheJudge:
    """Where the numbers were still coming from after the rates were removed.

    The extraction prompt's own example taught the model to write them:
    "alternates short declarative sentences (3-8 words) with longer explanatory
    ones (15-25 words)". The Brain carried that into the voice pass, which
    faulted a draft for missing a rhythm it quoted as "(10-15 words)" in one
    round and "(10-20 words)" in the next. The same corpus, two different
    numbers, both invented.
    """

    def test_a_range_is_removed_and_the_move_survives(self):
        from utils.brand_profile import strip_invented_counts

        line = ("Sentence rhythm: alternates very short sentences (2-8 words) with slightly "
                "longer ones (10-15 words) to create a punchy, varied pace.")
        cleaned = strip_invented_counts(line)
        assert "2-8" not in cleaned and "10-15" not in cleaned
        assert "alternates very short sentences with slightly longer ones" in cleaned
        assert "punchy, varied pace" in cleaned

    def test_single_bounds_go_too(self):
        from utils.brand_profile import strip_invented_counts

        assert "40" not in strip_invented_counts("Keep paragraphs under 40 words for scannability.")
        assert "three" in strip_invented_counts("Uses three-beat escalation.")

    def test_numbers_that_are_not_prescriptions_are_left_alone(self):
        from utils.brand_profile import strip_invented_counts

        for kept in ("Cites 40 retail clients as proof.", "Opens on Act 1 with a cold scene."):
            assert strip_invented_counts(kept) == kept

    def test_the_extraction_prompt_no_longer_teaches_them(self):
        from prompts.metrics import METRICS_EXTRACTION

        assert "(3-8 words)" not in METRICS_EXTRACTION
        assert "Never write a number of words" in METRICS_EXTRACTION

    def test_the_enforcer_scrubs_the_craft_it_passes_on(self):
        import inspect

        from nodes import enforcer

        source = inspect.getsource(enforcer.enforcer_node)
        assert "strip_invented_counts(" in source, (
            "an instruction in a prompt is not a guarantee; the codebase checks instead"
        )


class TestRhythmIsDescribedAsAMix:
    """Told only "sentences are short", the writer produced a script whose every
    line was four words long. The corpus it was imitating runs a third of its
    sentences short, half middling and a sixth longer."""

    def _habits(self):
        import glob
        import os

        documents = [open(p, encoding="utf-8").read()
                     for p in glob.glob(os.path.join(FIXTURES, "voice", "*.txt"))]
        spec = render_spec(corpus_spec(documents), beat_grammar(documents))
        return spec.split("SENTENCE AND PARAGRAPH HABITS:")[1].split("HOW ITS")[0]

    def test_it_says_the_sentences_vary(self):
        habits = self._habits()
        assert "not uniformly short" in habits
        assert "Vary them" in habits

    def test_it_names_flatness_as_the_failure(self):
        assert "reads as a list whatever that length is" in self._habits()

    def test_it_still_quotes_no_rates(self):
        import re

        assert not re.search(r"\d", self._habits())


class TestRhythmIsMeasuredNotJudged:
    """The calibration run that forced this. Against the live Brain the judge
    scored this brand's hand-written gold 7.0 and faulted it for "Sentence
    rhythm" — while rating a draft that had turned "more than seven men" into
    "Seven men sat together" above it.

    Rhythm is the one thing about voice that is measurable and the one thing a
    judge cannot measure by reading. Every oscillation this system has had was a
    judge ruling on it: join these sentences, then split them, then join them.
    """

    @pytest.fixture(scope="class")
    def pair(self):
        from tests.gold_pairs import discover

        return discover()[0]

    def test_the_gold_is_not_flat(self, pair):
        from utils.voice_spec import flatness_note

        assert flatness_note(pair.gold, pair.brain) == "", (
            "the answer this brand's reviewer wrote must not be called flat"
        )

    def test_the_drafts_that_were_genuinely_flat_are_caught(self, pair):
        from utils.voice_spec import flatness_note

        flat = {d.name for d in pair.rejected if flatness_note(d.content, pair.brain)}
        assert {"dropped_the_qualifier", "padded_register"} <= flat

    def test_a_draft_whose_fault_was_something_else_is_left_alone(self, pair):
        """It summarised its ending away. Its rhythm was never the problem, and
        a check that fires on everything says nothing."""
        from utils.voice_spec import flatness_note

        draft = next(d for d in pair.rejected if d.name == "summarised_the_ending")
        assert flatness_note(draft.content, pair.brain) == ""

    def test_the_brands_own_range_would_have_condemned_the_gold(self, pair):
        """Why this is a share of the brand's variation rather than its band:
        the corpus sits at 3.38-3.49 and the gold at 2.7. A hand-written
        retelling is tighter than a full screenplay and right to be."""
        from utils.voice_spec import _band_from_metrics, prose_only, shape_metrics

        band = _band_from_metrics(pair.brain, "words_per_sentence_variation")
        gold = shape_metrics(prose_only(pair.gold))["words_per_sentence_variation"]
        assert gold < band["low"], "the gold is outside the band and must still pass"

    def test_the_flattest_possible_draft_is_caught(self, pair):
        """Zero variation is a measurement, not a missing one. The first
        version of this check treated 0.0 as "nothing measured" and waved
        through the one draft it should have been surest about."""
        from utils.voice_spec import flatness_note, shape_metrics

        flat = " ".join([
            "He walks the road.", "She waits by it.", "They watch the gate.",
            "He counts the men.", "She holds the bag.", "They leave the yard.",
            "He climbs the wall.", "She calls the name.", "They cross the line.",
            "He drops the key.", "She finds the door.", "They open the box.",
            "He reads the note.", "She burns the page.",
        ])
        assert shape_metrics(flat)["words_per_sentence_variation"] == 0.0
        assert flatness_note(flat, pair.brain), "every sentence the same length went unremarked"

    def test_a_short_piece_is_not_judged_on_its_rhythm(self):
        from utils.voice_spec import flatness_note

        brain = ("# MEASURED MECHANICS\n- words_per_sentence_variation: 3.4\n"
                 "- words_per_sentence_variation_low: 3.3\n- words_per_sentence_variation_high: 3.5\n")
        assert flatness_note("He waits. Nobody comes. The road is closed.", brain) == ""

    def test_the_judge_is_told_to_leave_rhythm_alone(self):
        from prompts.enforcer import ENFORCER_PROMPT

        assert "Sentence rhythm, sentence length, and how much they vary" in ENFORCER_PROMPT
        assert "measured before you" in ENFORCER_PROMPT


class TestTheJudgeIsNotShownTheRhythmRules:
    """Forbidding it was not enough. Told plainly that rhythm was measured
    elsewhere and none of its business, the judge quoted this brand's own rule
    back as its reason for refusing four drafts in one run:

        The brand voice requires *variation* in sentence length: 'Sentences are
        short, and not uniformly short. Clipped ones carry the action...'

    An instruction not to use information loses to the information.
    """

    @pytest.fixture(scope="class")
    def spec(self):
        from tests.gold_pairs import discover
        from utils.brand_profile import extract_section

        return extract_section(discover()[0].brain, "VOICE SPEC")

    def test_the_writer_still_gets_them(self, spec):
        from utils.voice_spec import RHYTHM_BLOCK

        assert RHYTHM_BLOCK in spec
        assert "not uniformly short" in spec and "Vary them" in spec

    def test_the_judge_does_not(self, spec):
        from utils.voice_spec import for_judge

        judged = for_judge(spec)
        for rule in ("not uniformly short", "Vary them", "reads as a list",
                     "past twenty words", "end early"):
            assert rule not in judged, f"the judge can still cite {rule!r}"

    def test_everything_else_survives_the_trim(self, spec):
        from utils.voice_spec import for_judge

        judged = for_judge(spec)
        assert "HOW ITS OPENINGS TEND TO GO" in judged
        assert "Abstract nouns" in judged and "Plain words" in judged
        assert "Nothing here is a template" in judged

    def test_a_spec_without_a_rhythm_block_is_returned_whole(self):
        from utils.voice_spec import for_judge

        assert for_judge("SENTENCE AND PARAGRAPH HABITS:\n- Plain words.") == (
            "SENTENCE AND PARAGRAPH HABITS:\n- Plain words."
        )
        assert for_judge("") == ""

    def test_the_enforcer_trims_before_handing_it_over(self):
        import inspect

        from nodes import enforcer

        assert "for_judge(" in inspect.getsource(enforcer.enforcer_node)


class TestTheBriefsOwnCommentaryIsNotDemanded:
    """Two checks were pulling against each other. Coverage demanded the words
    of "This is the moment that marks the beginning of his entanglement...
    widely regarded as the most feared cult" — mark, beginning, regarded,
    eventually — and when a draft put them in, the judge refused it for
    borrowed commentary. Both were right; the demand was wrong."""

    @pytest.fixture(scope="class")
    def pair(self):
        from tests.gold_pairs import discover

        return discover()[0]

    def test_a_retelling_is_not_asked_for_the_briefs_framing(self, pair):
        from utils.coverage import dropped_detail

        told = (
            "Kan tries to leave. He says he must get back to his hostel.\n"
            "He finds more than seven men, seated, drinking. They tell him to drink. He says no.\n"
            "Other men greet EMK with both hands. EMK asks about his studies and gives him a number.\n"
        )
        missing = " ".join(w for f in dropped_detail(told, pair.brief, 3.1) for w in f["missing"])
        for framing in ("mark", "beginn", "regard", "eventually"):
            assert framing not in missing, f"the draft was asked for {framing!r}"

    def test_the_facts_in_that_sentence_are_still_demanded(self, pair):
        """Dropping "widely regarded" must not drop "cult" or "deadliest"."""
        from utils.coverage import content_words

        beat = ("This is the moment that marks the beginning of his entanglement with the Viking "
                "Confraternity, widely regarded as the most feared cult in the state.")
        words = content_words(beat)
        assert {"cult", "state"} <= words, "the facts in the sentence are still story"
        # Words are stemmed before they are compared, which is why the exclusion
        # list is stemmed too: written out in full, "beginning" was compared as
        # "beginn" and matched nothing.
        assert not ({"mark", "beginn", "regard", "moment"} & words)
