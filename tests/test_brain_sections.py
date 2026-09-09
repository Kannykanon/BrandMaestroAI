"""
Tests for reading one prose section out of a synthesised brain.

The punctuation rules that decide whether a brand permits a mark are read out
of these sections, so a boundary that fails to find the next header does not
degrade gracefully — it reads the rest of the brain as punctuation rules. That
happened live: a lookahead of [A-Z_]+: matched none of the brain's own headers,
because they contain spaces and ampersands, and the word "avoid" from a later
METAPHOR & ANALOGY section stripped every question mark out of every draft.
"""

from utils.brand_profile import brand_prose_section
from utils.enforcement.punctuation import sanitize_banned_punctuation

# Headers copied from a live brain, including the multi-word and punctuated ones
# that the old boundary could not match.
BRAIN = """# BRAND VOICE PROFILE

PRONOUN PATTERN:
First-person plural dominant.

PUNCTUATION HABITS:
Em dashes are used freely. Semicolons appear regularly.

CAPITALIZATION STYLE:
Standard sentence case, with film titles in full caps.

QUESTION USAGE:
Questions are used sparingly, either as rhetorical devices or as direct
inquiries to engage the reader.

METAPHOR & ANALOGY:
The brand favors direct language and avoids extended, decorative metaphors.

SECTION PATTERN:
Formal heading hierarchies are generally absent.

NAMED FRAMEWORKS & METHODOLOGIES:
Insufficient data.

DON'T:
Never invent a number.

# MEASURED MECHANICS
- exclamation_marks_per_100_words: 0.3
- question_marks_per_100_words: 0.3
- nominalisations_per_100_words: 0.9
"""


class TestBrandProseSection:
    def test_stops_at_the_next_multi_word_header(self):
        section = brand_prose_section(BRAIN, "QUESTION USAGE")
        assert "sparingly" in section
        # Everything that follows belongs to other sections.
        assert "avoids" not in section, "ran past METAPHOR & ANALOGY"
        assert "absent" not in section, "ran past SECTION PATTERN"
        assert "Never invent" not in section, "ran past DON'T"

    def test_stops_before_the_measured_mechanics_block(self):
        section = brand_prose_section(BRAIN, "NAMED FRAMEWORKS & METHODOLOGIES")
        assert "Insufficient data" in section
        assert "per_100_words" not in section

    def test_reads_a_section_with_an_ampersand_in_its_own_header(self):
        section = brand_prose_section(BRAIN, "METAPHOR & ANALOGY")
        assert "direct language" in section
        assert "heading hierarchies" not in section

    def test_captures_the_whole_section_and_no_more(self):
        section = brand_prose_section(BRAIN, "PUNCTUATION HABITS")
        assert "Em dashes" in section and "Semicolons" in section
        assert "sentence case" not in section

    def test_a_missing_section_is_none(self):
        assert brand_prose_section(BRAIN, "NO SUCH SECTION") is None

    def test_the_boundary_is_not_fooled_by_a_mid_sentence_colon(self):
        brain = (
            "QUESTION USAGE:\nQuestions are rare. One note: they are answered.\n"
            "METAPHOR & ANALOGY:\nAvoided entirely.\n"
        )
        section = brand_prose_section(brain, "QUESTION USAGE")
        assert "they are answered" in section
        assert "Avoided" not in section


class TestQuestionMarksSurviveWhenTheBrandUsesThem:
    DRAFT = "Did you think you knew this story? We did too. It runs 11 minutes."

    def test_a_measured_rate_protects_the_mark(self):
        """
        The regression: 'used sparingly' plus 'avoids' bleeding in from a later
        section removed every question mark, which turns a question into a
        sentence ending in a full stop rather than into fewer questions.
        """
        fixed, applied = sanitize_banned_punctuation(self.DRAFT, BRAIN)
        assert "?" in fixed, f"question mark stripped from a brand that uses them: {applied}"
        assert fixed == self.DRAFT

    def test_a_brand_that_never_uses_them_still_has_them_removed(self):
        brain = BRAIN.replace(
            "- question_marks_per_100_words: 0.3",
            "- question_marks_per_100_words: 0.0",
        ).replace(
            "Questions are used sparingly, either as rhetorical devices or as direct\ninquiries to engage the reader.",
            "Questions are never used.",
        )
        fixed, applied = sanitize_banned_punctuation(self.DRAFT, brain)
        assert "?" not in fixed
        assert any("question" in a for a in applied)
