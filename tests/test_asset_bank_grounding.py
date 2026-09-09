"""
Tests for the asset-bank grounding filter.

The brand brain presents asset_bank entries to the writer as the closed list of
facts it is permitted to state. Two things must hold for that list to be
trustworthy: entries extracted from a document must actually appear in it, and
generated content must not be able to add entries to it at all.
"""

from brand_metrics import (
    _FACT_BEARING_FIELDS,
    _TOPIC_COUPLED_FIELDS,
    _ground_asset_bank,
    _strip_lifted_quotations,
    _normalise_for_grounding,
    _keep_voice_signal_only,
)

DOC = (
    "Ninebark Films has spent 17 years making documentaries. "
    "We looked at 240 documentaries this year. "
    "We run the Ninebark Field Method on every shoot. "
    "Documentaries, and nothing else."
)


def _bank(**fields):
    return {"brand_name": "Ninebark Films", "asset_bank": dict(fields), "style": {"x": 1}}


class TestGroundAssetBank:
    def test_keeps_claims_present_in_the_document(self):
        cleaned, dropped = _ground_asset_bank(
            _bank(social_proof_claims=["We looked at 240 documentaries this year."]), DOC
        )
        assert dropped == []
        assert cleaned["asset_bank"]["social_proof_claims"] == [
            "We looked at 240 documentaries this year."
        ]

    def test_drops_claims_absent_from_the_document(self):
        cleaned, dropped = _ground_asset_bank(
            _bank(stated_values=["We are architects of meaning."]), DOC
        )
        assert cleaned["asset_bank"]["stated_values"] == []
        assert len(dropped) == 1
        assert "architects of meaning" in dropped[0]

    def test_a_changed_number_is_not_grounded(self):
        """Grounding must be strict about facts — 240 and 250 are not the same claim."""
        cleaned, dropped = _ground_asset_bank(
            _bank(social_proof_claims=["We looked at 250 documentaries this year."]), DOC
        )
        assert cleaned["asset_bank"]["social_proof_claims"] == []
        assert len(dropped) == 1

    def test_typography_differences_do_not_cause_a_false_drop(self):
        doc = "We’ve spent 17 years — documentaries only."
        cleaned, dropped = _ground_asset_bank(
            _bank(stated_values=["We've spent 17 years - documentaries only."]), doc
        )
        assert dropped == []
        assert len(cleaned["asset_bank"]["stated_values"]) == 1

    def test_line_wrapping_does_not_cause_a_false_drop(self):
        doc = "We run the Ninebark\n   Field Method on every shoot."
        cleaned, dropped = _ground_asset_bank(
            _bank(named_frameworks=["Ninebark Field Method"]), doc
        )
        assert dropped == []

    def test_placeholder_values_are_dropped_without_being_reported(self):
        cleaned, dropped = _ground_asset_bank(
            _bank(financial_targets=["None", "N/A", ""]), DOC
        )
        assert cleaned["asset_bank"]["financial_targets"] == []
        assert dropped == []

    def test_every_fact_bearing_field_is_checked(self):
        fields = {field: ["a claim that is nowhere in the source"] for field in _FACT_BEARING_FIELDS}
        cleaned, dropped = _ground_asset_bank(_bank(**fields), DOC)
        assert len(dropped) == len(_FACT_BEARING_FIELDS)
        for field in _FACT_BEARING_FIELDS:
            assert cleaned["asset_bank"][field] == []

    def test_voice_fields_are_left_untouched(self):
        profile = _bank(stated_values=["invented"])
        profile["signature_constructions"] = ["two-part contrast sentence"]
        cleaned, _ = _ground_asset_bank(profile, DOC)
        assert cleaned["signature_constructions"] == ["two-part contrast sentence"]
        assert cleaned["brand_name"] == "Ninebark Films"

    def test_a_string_instead_of_a_list_is_handled(self):
        cleaned, dropped = _ground_asset_bank(
            _bank(named_frameworks="Ninebark Field Method"), DOC
        )
        assert cleaned["asset_bank"]["named_frameworks"] == ["Ninebark Field Method"]
        assert dropped == []

    def test_missing_asset_bank_is_a_no_op(self):
        profile = {"style": {"x": 1}}
        cleaned, dropped = _ground_asset_bank(profile, DOC)
        assert cleaned == profile
        assert dropped == []

    def test_does_not_mutate_the_input(self):
        profile = _bank(stated_values=["invented"])
        _ground_asset_bank(profile, DOC)
        assert profile["asset_bank"]["stated_values"] == ["invented"]


class TestKeepVoiceSignalOnly:
    def test_removes_every_fact_bearing_field(self):
        stripped = _keep_voice_signal_only(
            _bank(**{field: ["something"] for field in _FACT_BEARING_FIELDS})
        )
        for field in _FACT_BEARING_FIELDS:
            assert field not in stripped["asset_bank"]

    def test_a_generation_cannot_rename_the_brand(self):
        assert "brand_name" not in _keep_voice_signal_only(_bank())

    def test_keeps_the_voice_fields_the_feedback_loop_exists_for(self):
        profile = _bank(stated_values=["invented"])
        profile["tone"] = {"register": "conversational"}
        profile["structure"] = {"argumentation_style": "storytelling"}
        profile["signature_constructions"] = ["two-part contrast sentence"]
        profile["style"] = {
            "punctuation_habits": "no semicolons",
            "mechanical_rules": {"sentence_rhythm": "short then long"},
        }
        stripped = _keep_voice_signal_only(profile)
        assert stripped["tone"] == {"register": "conversational"}
        assert stripped["structure"] == {"argumentation_style": "storytelling"}
        assert stripped["signature_constructions"] == ["two-part contrast sentence"]
        assert stripped["style"]["punctuation_habits"] == "no semicolons"
        assert stripped["style"]["mechanical_rules"] == {"sentence_rhythm": "short then long"}

    def test_drops_every_topic_coupled_field(self):
        profile = _bank()
        profile["generation_instructions"] = "open with a formal announcement of a film"
        profile["style"] = {
            "metaphor_usage": "extended metaphor drawn from a classic fable",
            "punctuation_habits": "keep me",
        }
        profile["intellectual_patterns"] = {
            "authority_source": "the timeless wisdom of a classic fable and an old storyteller",
            "value_hierarchy": "perseverance ranked above talent",
            "diagnostic_style": "keep me",
        }
        stripped = _keep_voice_signal_only(profile)
        assert "generation_instructions" not in stripped
        assert "metaphor_usage" not in stripped["style"]
        assert "authority_source" not in stripped["intellectual_patterns"]
        assert "value_hierarchy" not in stripped["intellectual_patterns"]
        # the siblings of a dropped nested key survive
        assert stripped["style"]["punctuation_habits"] == "keep me"
        assert stripped["intellectual_patterns"]["diagnostic_style"] == "keep me"

    def test_the_declared_topic_coupled_paths_are_all_actually_dropped(self):
        """Guards against a path being added to the tuple but never taking effect."""
        for path in _TOPIC_COUPLED_FIELDS:
            head, _, rest = path.partition(".")
            profile = {head: {rest: "topic"} if rest else "topic"}
            stripped = _keep_voice_signal_only(profile)
            if rest:
                assert rest not in stripped.get(head, {}), path
            else:
                assert head not in stripped, path

    def test_a_missing_nested_parent_is_not_created(self):
        stripped = _keep_voice_signal_only({"style": {"punctuation_habits": "x"}})
        assert "intellectual_patterns" not in stripped

    def test_a_non_dict_nested_parent_is_left_alone(self):
        profile = {"style": "conversational"}
        assert _keep_voice_signal_only(profile)["style"] == "conversational"

    def test_does_not_mutate_a_nested_field_of_the_input(self):
        profile = {"style": {"metaphor_usage": "fables", "punctuation_habits": "x"}}
        _keep_voice_signal_only(profile)
        assert profile["style"]["metaphor_usage"] == "fables"

    def test_keeps_non_fact_asset_bank_keys(self):
        profile = _bank(stated_values=["invented"])
        profile["asset_bank"]["some_future_voice_key"] = ["keep me"]
        stripped = _keep_voice_signal_only(profile)
        assert stripped["asset_bank"]["some_future_voice_key"] == ["keep me"]

    def test_the_regression_this_fix_exists_for(self):
        """
        A tortoise-and-hare kids script must not teach a documentary studio that
        it values mindfulness. Every claim observed poisoning the live brain came
        through this path.
        """
        observed = _bank(stated_values=[
            "We are architects of meaning; our stories are built to last.",
            "perseverance",
            "mindfulness",
            "True triumph is not found in a sprint. It is in the steady pace of the long haul.",
        ])
        assert _keep_voice_signal_only(observed)["asset_bank"] == {}

    def test_does_not_mutate_the_input(self):
        profile = _bank(stated_values=["invented"])
        _keep_voice_signal_only(profile)
        assert profile["asset_bank"]["stated_values"] == ["invented"]
        assert profile["brand_name"] == "Ninebark Films"

    def test_non_dict_is_returned_unchanged(self):
        assert _keep_voice_signal_only(None) is None


class TestNormalisation:
    def test_collapses_whitespace_and_case(self):
        assert _normalise_for_grounding("  We   Only\nDo\tDocumentaries. ") == (
            "we only do documentaries."
        )

    def test_folds_curly_quotes_and_dashes(self):
        assert _normalise_for_grounding("“We’ve” — yes") == '"we\'ve" - yes'


class TestStripLiftedQuotations:
    """
    The extraction prompt asks the pattern fields to describe the move, not the
    words. A quotation that is genuinely a template is signal; one copied out of
    the draft is a verbatim seed planted in the brain.
    """

    DRAFT = (
        "Our narrative dissects the hare's undoing, revealing it as a direct "
        "consequence of his profound hubris and akrasia. True victory is forged "
        "through internal strength, not just external pace."
    )

    def test_removes_a_phrase_copied_out_of_the_draft(self):
        profile = {"signature_constructions": [
            "describes character flaws as direct consequences of internal states: "
            "'revealing it as a direct consequence of his profound hubris and akrasia.'"
        ]}
        cleaned, removed = _strip_lifted_quotations(profile, self.DRAFT)
        assert len(removed) == 1
        kept = cleaned["signature_constructions"][0]
        assert "hubris" not in kept
        assert kept == "describes character flaws as direct consequences of internal states"

    def test_keeps_an_abstract_template(self):
        profile = {"intellectual_patterns": {
            "reframing_moves": "often using an 'X isn't Y, it's Z' structure."
        }}
        cleaned, removed = _strip_lifted_quotations(profile, self.DRAFT)
        assert removed == []
        assert "X isn't Y, it's Z" in cleaned["intellectual_patterns"]["reframing_moves"]

    def test_typography_does_not_let_a_lift_through(self):
        profile = {"signature_constructions": [
            "closes on \u201cTrue victory is forged through internal strength\u201d"
        ]}
        cleaned, removed = _strip_lifted_quotations(profile, self.DRAFT)
        assert len(removed) == 1
        assert "internal strength" not in cleaned["signature_constructions"][0]

    def test_short_quotations_are_left_alone(self):
        """Two or three quoted words are a label, not a lifted sentence."""
        profile = {"style": {"pronoun_pattern": "uses 'we' as subject"}}
        cleaned, removed = _strip_lifted_quotations(profile, "we we we")
        assert removed == []
        assert cleaned["style"]["pronoun_pattern"] == "uses 'we' as subject"

    def test_walks_nested_lists_and_dicts(self):
        profile = {"intellectual_patterns": {"thinking_templates": [
            "opens plainly",
            "then 'True victory is forged through internal strength'",
        ]}}
        cleaned, removed = _strip_lifted_quotations(profile, self.DRAFT)
        assert len(removed) == 1
        assert cleaned["intellectual_patterns"]["thinking_templates"][0] == "opens plainly"

    def test_non_string_values_survive(self):
        profile = {"style": {"formality": 0.8, "flag": True, "missing": None}}
        cleaned, removed = _strip_lifted_quotations(profile, self.DRAFT)
        assert cleaned["style"] == {"formality": 0.8, "flag": True, "missing": None}
        assert removed == []

    def test_does_not_mutate_the_input(self):
        profile = {"signature_constructions": [
            "x: 'revealing it as a direct consequence of his profound hubris and akrasia.'"
        ]}
        _strip_lifted_quotations(profile, self.DRAFT)
        assert "hubris" in profile["signature_constructions"][0]

    def test_removing_a_quote_does_not_leave_a_dangling_article(self):
        """
        The live artefact: stripping the quote out of this left "framing it as
        missing a or overlooking profound lessons", and that is what the
        synthesis LLM had to read.
        """
        profile = {"intellectual_patterns": {"diagnostic_style":
            "framing it as missing a 'deeper truth' or overlooking profound lessons."
        }}
        cleaned, removed = _strip_lifted_quotations(
            profile, "The classic moral often misses a deeper truth behind the line."
        )
        assert len(removed) == 1
        text = cleaned["intellectual_patterns"]["diagnostic_style"]
        assert text == "framing it as missing or overlooking profound lessons."
        assert " a or " not in text
