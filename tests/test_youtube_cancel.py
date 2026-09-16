"""Stopping a running step, deleting a project while it runs, and continuing.

Before this, a project that had started a step could only be waited out. The
status was claimed for the duration ("the project is rendering; wait for it to
finish"), delete was refused on the same test, and there was no way to say stop
— so a render heading in the wrong direction had to be paid for to the end.

Worse was the worker that never came back. The claim was written to the row and
nothing ever cleared it, so a project whose container was replaced mid-render
stayed "rendering" for ever: every button refused, including the ones that
would have got it moving again.
"""
from datetime import datetime, timedelta, timezone

import pytest

import youtube.cancel as cancel
import youtube.projects as projects
import youtube.render as render
import youtube.storyboard as storyboard
from tests.test_youtube_storyboard import env, ready_project  # noqa: F401  (fixture)


def voiced_project(env):
    project, _, _ = ready_project(env, voiced=True)
    return project


class TestStoppingAStep:
    def test_voicing_stops_between_shots_and_keeps_what_it_voiced(self, env):
        project, _, _ = ready_project(env, voiced=False)
        shots = projects._shots(env.db, project)
        assert len(shots) > 2, "need several shots to stop part-way through"

        # Stop as soon as the second shot has been voiced, the way the API's
        # stop endpoint does: by writing the flag, not by killing anything.
        original = cancel.check
        voiced = []

        def stop_after_two(db, project_id, did=""):
            voiced.append(did)
            if len(voiced) == 3:
                cancel.request(db, projects.get_project(db, "biz", project_id))
            return original(db, project_id, did)

        cancel.check = stop_after_two
        try:
            with pytest.raises(cancel.Cancelled):
                projects.voice_project(env.db, project, env.storage)
        finally:
            cancel.check = original

        kept = [s for s in projects._shots(env.db, project) if s.audio_key]
        assert len(kept) == 2, "the shots already voiced were thrown away"

    def test_a_stopped_project_is_not_a_failed_one(self, env):
        project, _, _ = ready_project(env, voiced=False)
        projects.mark_busy(env.db, project, "voicing")
        projects.stopped(env.db, project, "Stopped after 2 of 6 shots voiced.")
        assert project.status != "failed"
        assert project.status == "cast", "should fall back to what the project actually has"
        assert "Stopped" in project.error

    def test_continuing_only_does_the_work_that_is_left(self, env):
        """Re-running the same step is what 'continue' means: every step skips
        the shots that already have their output."""
        project, _, _ = ready_project(env, voiced=False)
        shots = projects._shots(env.db, project)
        projects.voice_project(env.db, project, env.storage)
        first_keys = {s.id: s.audio_key for s in projects._shots(env.db, project)}

        env.db.expire_all()
        projects.voice_project(env.db, project, env.storage)
        assert {s.id: s.audio_key for s in projects._shots(env.db, project)} == first_keys

    def test_a_stop_asked_for_last_time_does_not_stop_this_run(self, env):
        project, _, _ = ready_project(env, voiced=False)
        cancel.request(env.db, project)
        projects.mark_busy(env.db, project, "voicing")
        assert project.cancel_requested_at is None
        cancel.check(env.db, project.id)  # does not raise


class TestDeletingWhileItRuns:
    def test_delete_is_allowed_while_a_step_is_running(self, env):
        project = voiced_project(env)
        projects.mark_busy(env.db, project, "rendering")
        project_id = project.id
        projects.delete_project(env.db, project, env.storage)
        assert projects.get_project(env.db, "biz", project_id) is None

    def test_the_running_step_stops_at_its_next_checkpoint(self, env):
        project = voiced_project(env)
        projects.mark_busy(env.db, project, "rendering")
        project_id = project.id
        projects.delete_project(env.db, project, env.storage)
        with pytest.raises(cancel.Cancelled, match="deleted"):
            cancel.check(env.db, project_id)

    def test_deleting_an_idle_project_still_works(self, env):
        project = voiced_project(env)
        project_id = project.id
        projects.delete_project(env.db, project, env.storage)
        assert projects.get_project(env.db, "biz", project_id) is None


class TestAWorkerThatNeverCameBack:
    def test_a_fresh_claim_is_not_treated_as_abandoned(self, env):
        project = voiced_project(env)
        projects.mark_busy(env.db, project, "rendering")
        assert not cancel.is_stale(project)
        with pytest.raises(projects.ProjectError, match="rendering"):
            projects._require_not_busy(project)

    def test_a_claim_older_than_the_step_could_possibly_run_is_abandoned(self, env):
        project = voiced_project(env)
        projects.mark_busy(env.db, project, "rendering")
        project.busy_since = datetime.now(timezone.utc) - timedelta(hours=4)
        env.db.commit()
        assert cancel.is_stale(project)
        projects._require_not_busy(project)  # no longer refuses everything

    def test_a_claim_made_before_this_column_existed_is_abandoned(self, env):
        """Rows claimed by the deploy that is being replaced have no
        busy_since. Leaving them stuck for ever is the bug, not the fix."""
        project = voiced_project(env)
        projects.mark_busy(env.db, project, "rendering")
        project.busy_since = None
        env.db.commit()
        assert cancel.is_stale(project)

    def test_an_idle_project_is_never_stale(self, env):
        project = voiced_project(env)
        assert project.status not in projects.BUSY_STATUSES
        assert not cancel.is_stale(project)


class TestWhatTheUiIsTold:
    def test_a_running_project_says_it_is_running(self, env):
        project = voiced_project(env)
        projects.mark_busy(env.db, project, "rendering")
        data = projects.serialize_project(env.db, project)
        assert data["busy"] is True and data["stopping"] is False
        assert data["abandoned"] is False
        assert data["resumable_step"] == "render"

    def test_a_project_asked_to_stop_says_so_rather_than_looking_hung(self, env):
        project = voiced_project(env)
        projects.mark_busy(env.db, project, "rendering")
        cancel.request(env.db, project)
        assert projects.serialize_project(env.db, project)["stopping"] is True

    def test_an_abandoned_project_is_reported_as_abandoned(self, env):
        project = voiced_project(env)
        projects.mark_busy(env.db, project, "drawing")
        project.busy_since = datetime.now(timezone.utc) - timedelta(hours=5)
        env.db.commit()
        assert projects.serialize_project(env.db, project)["abandoned"] is True

    def test_an_idle_project_offers_nothing_to_resume(self, env):
        data = projects.serialize_project(env.db, voiced_project(env))
        assert data["busy"] is False and data["resumable_step"] is None
