"""
Tests for the asset-bank grounding filter.

The brand brain presents asset_bank entries to the writer as the closed list of
facts it is permitted to state, and the extraction prompt requires them to be
copied verbatim out of the source document. This checks that requirement rather
than trusting it.

The other half of the problem is gone rather than filtered: generations are no
longer extracted into the Brain at all, so nothing can add a claim to this list
except an uploaded document. See test_brain_is_documents_only.py.
"""

from brand_metrics import (
    _FACT_BEARING_FIELDS,
    _ground_asset_bank,
    _normalise_for_grounding,
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


class TestNormalisation:
    def test_collapses_whitespace_and_case(self):
        assert _normalise_for_grounding("  We   Only\nDo\tDocumentaries. ") == (
            "we only do documentaries."
        )

    def test_folds_curly_quotes_and_dashes(self):
        assert _normalise_for_grounding("“We’ve” — yes") == '"we\'ve" - yes'
