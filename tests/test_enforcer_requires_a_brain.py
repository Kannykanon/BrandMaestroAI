"""The Enforcer refuses to score a draft when there is no Brand Brain.

Observed live, on a documentary distributor's trailer copy. The brand context
for that content type was empty, and the scoring model said so:

    "The evaluation cannot confirm that the content replicates the brand's
     writing mechanics because the BRAND METRICS section is empty."  -> 1.0

It returned 1.0 twice for that reason. Then, on the identical empty input, it
returned 8.8 and approved. Nothing about the brain had changed between those
passes; the model had simply sampled differently. A structural precondition
cannot live in the scoring prompt, for the same reason the hallucination and
publishability verdicts are re-applied in code — an APPROVAL RULE is not an
enforcement mechanism.

What shipped at 8.8 makes the case on its own. With no brain:
  - the writer had fallen through to its generic first-person-plural fallback,
    so the copy was in the system's default voice, not the brand's;
  - run_preflight_checks found no PUNCTUATION HABITS and checked nothing;
  - check_measured_mechanics found no MEASURED MECHANICS and returned nothing;
  - find_unfilled_placeholders had no measured bracket rate, so a literal
    "[Movie Title]" went out in approved copy.
"""
import pytest

import graph.deps
from nodes.enforcer import enforcer_node
from utils.brand_profile import brand_brain_is_usable
from utils.enforcement import MAX_ITERATIONS


class _Analyzer:
    def __init__(self, context):
        self._context = context

    def get_context(self):
        return self._context


class _ModelWasCalled(Exception):
    """Raised in place of an LLM call, to prove the guard did not stop here."""


@pytest.fixture
def enforcer(monkeypatch):
    """Run enforcer_node against a given brand context, with no LLM and no DB."""

    def run(context, **state):
        monkeypatch.setattr(
            graph.deps,
            "resolve_deps",
            lambda business_id, content_type: (None, _Analyzer(context), None),
        )
        # Any model call past the guard raises, so a test can tell "refused
        # early" from "went on to score it" without a network round trip.
        monkeypatch.setattr(
            "model.LLMSingleton.get",
            lambda *a, **k: (_ for _ in ()).throw(_ModelWasCalled()),
        )
        base = {
            "business_id": "b1",
            "content_type": "trailer_copy",
            "topic": "a tortoise and a hare run a race",
            "research": "",
            "content": "We observe that certain events capture attention. [Movie Title]",
            "iteration": 1,
        }
        return enforcer_node({**base, **state})

    return run


# The states that actually occur: no metric rows for this content type (the
# brain is built per business AND content type, so documents uploaded under
# another type leave this one empty), a cache holding a blank, and prose with
# none of the headers every downstream reader keys off.
EMPTY_CONTEXTS = [
    pytest.param("", id="no-rows"),
    pytest.param("   \n\n  ", id="whitespace"),
    pytest.param("Insufficient data to synthesise a profile.", id="no-headers"),
]


class TestAnEmptyBrainIsRefused:
    @pytest.mark.parametrize("context", EMPTY_CONTEXTS)
    def test_it_is_not_approved(self, enforcer, context):
        assert enforcer(context)["approved"] is False, (
            "approved a draft with nothing to approve it against"
        )

    @pytest.mark.parametrize("context", EMPTY_CONTEXTS)
    def test_it_scores_zero(self, enforcer, context):
        assert enforcer(context)["score"] == 0.0

    @pytest.mark.parametrize("context", EMPTY_CONTEXTS)
    def test_no_model_call_is_spent_on_it(self, enforcer, context):
        """The guard runs before the scoring call, not after it."""
        enforcer(context)  # _ModelWasCalled would escape if it got that far

    def test_the_feedback_names_the_actual_problem(self, enforcer):
        feedback = enforcer("")["feedback"]
        assert "NO BRAND BRAIN" in feedback
        assert "trailer_copy" in feedback, "say which content type has no brain"
        assert "doc_role=voice" in feedback, "say how to fix it"

    def test_it_does_not_go_back_to_the_writer(self, enforcer):
        """
        No revision can supply a missing brain, so the run ends rather than
        spending two more rounds of LLM calls on a state the writer cannot
        change. graph._route_after_enforcer sends an unapproved draft at the
        iteration ceiling to the deployer.
        """
        assert enforcer("", iteration=1)["iteration"] >= MAX_ITERATIONS

    def test_max_iterations_cannot_rescue_it(self, enforcer):
        """
        The force-approval at the iteration ceiling only ever covers a quality
        shortfall. A missing brain is not one, and arriving at the ceiling must
        not turn it into an approval.
        """
        assert enforcer("", iteration=MAX_ITERATIONS)["approved"] is False

    def test_it_is_recorded_as_a_violation(self, enforcer):
        assert any(
            "brand brain" in v.lower()
            for v in enforcer("")["violation_history"]
        )


class TestARealBrainIsNotRefused:
    """The guard must not fire on a sparse-but-real brain."""

    BRAIN = (
        "# BRAND NAME\nNinebark Films\n\n"
        "# BRAND VOICE OVERVIEW\nPlain, specific, argumentative.\n"
    )

    def test_the_enforcer_goes_on_to_score_it(self, enforcer):
        with pytest.raises(_ModelWasCalled):
            enforcer(self.BRAIN)


class TestUsability:
    @pytest.mark.parametrize("context", ["", "   ", None, "prose with no headers"])
    def test_unusable(self, context):
        assert brand_brain_is_usable(context) is False

    @pytest.mark.parametrize("context", [
        "# BRAND NAME\nNinebark",
        "prose first\n# MEASURED MECHANICS\n- contractions_per_100_words: 3.8",
    ])
    def test_usable(self, context):
        assert brand_brain_is_usable(context) is True
