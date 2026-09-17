"""The rule the calibration script applies, without spending a model call.

The script itself (scripts/calibrate_judge.py) asks the voice pass to score
writing whose quality is already settled. That costs money, so it is never run
by the suite; what is checked here is the judgement it makes about the judge.
"""
from scripts.calibrate_judge import GOLD_EXPECTED, MIN_VOICE_SCORE, verdict


class TestWhenTheJudgeIsCalibrated:
    def test_the_gold_scores_well_and_beats_the_bad_drafts(self):
        ok, why = verdict(9.0, 5.5, True)
        assert ok and "calibrated" in why

    def test_a_pair_with_no_bad_drafts_only_needs_the_gold(self):
        assert verdict(8.5, 0.0, False)[0]


class TestWhenItIsNot:
    def test_refusing_the_brands_own_correct_answer_is_the_worst_case(self):
        """Below this the enforcer blocks outright, so the system would refuse
        the answer its reviewer wrote by hand."""
        ok, why = verdict(5.0, 0.0, False)
        assert not ok
        assert "REFUSE this brand's own correct answer" in why
        assert "anchor the judge with the gold" in why, "say what to do, not just what is wrong"

    def test_a_merely_lukewarm_gold_is_still_miscalibrated(self):
        ok, why = verdict(7.0, 0.0, False)
        assert not ok and "measuring something this brand does not do" in why

    def test_a_rubric_that_likes_everything_fails_too(self):
        """One draft turned "more than seven men" into "Seven men sat together"
        and shipped. A judge that rates it alongside the gold is not measuring."""
        ok, why = verdict(9.0, 9.2, True)
        assert not ok and "shipped wrongly scored 9.2" in why

    def test_a_tie_counts_against_it(self):
        assert not verdict(8.5, 8.5, True)[0]


def test_the_thresholds_are_the_ones_the_enforcer_uses():
    from nodes import enforcer
    import inspect

    assert f"MIN_VOICE_SCORE = {MIN_VOICE_SCORE}" in inspect.getsource(enforcer.enforcer_node)
    assert GOLD_EXPECTED > MIN_VOICE_SCORE
