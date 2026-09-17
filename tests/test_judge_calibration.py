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


class TestWhichBrainIsBeingScored:
    """An operator cleared their Brand Brain and every document, re-ran this,
    and got identical scores back. Nothing was broken: the script builds the
    Brain from the pair's own fixture documents, so it never reads the database
    at all. That is the right default — it asks whether the rubric is sound —
    but it has to say so, and there has to be a way to ask the other question.
    """

    def test_live_needs_to_be_told_which_brain(self):
        from scripts.calibrate_judge import main

        assert main(["--live"]) == 1, "a half-given --live must not fall back to fixtures"

    def test_the_default_says_which_brain_it_used(self, capsys, monkeypatch):
        import scripts.calibrate_judge as calibrate

        monkeypatch.setattr(calibrate, "report", lambda pair, brain="": True)
        assert calibrate.main([]) == 0
        printed = capsys.readouterr().out
        assert "fixture documents" in printed and "not a deployment" in printed

    def test_the_prompt_cache_is_off_for_the_run(self, capsys, monkeypatch):
        """Calls are cached in Redis on the exact prompt text. A second run of
        an unchanged rubric would be answered entirely from that cache —
        identical numbers, no model consulted, indistinguishable from a stable
        result."""
        import scripts.calibrate_judge as calibrate

        seen = {}
        monkeypatch.setattr("langchain_core.globals.set_llm_cache",
                            lambda cache: seen.update(cache=cache))
        monkeypatch.setattr(calibrate, "report", lambda pair, brain="": True)
        calibrate.main([])
        assert "cache" in seen and seen["cache"] is None

    def test_an_empty_live_brain_is_refused_rather_than_scored(self, monkeypatch):
        """Scoring against nothing would report the rubric's own numbers and
        call them the brand's."""
        import scripts.calibrate_judge as calibrate

        monkeypatch.setattr(calibrate, "live_brain", lambda business_id, content_type: "   ")
        assert calibrate.main(["--live", "biz", "script"]) == 1
