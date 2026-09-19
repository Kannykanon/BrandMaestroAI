"""A generation may not be killed and restarted while it is still working.

The worker killed generations at 300 seconds while the streaming endpoint waited
900 for the same task, and SoftTimeLimitExceeded — an ordinary Exception — fell
into the generic handler, which retried. One generation became up to three full
runs of a paid pipeline, ten seconds apart, each with its own research, its own
drafts and its own best-draft memory, all writing to one generation row.

That is visible in a run log as every event appearing twice with timestamps
drifting apart, and as a draft scored 9.2 by one attempt never being a candidate
when another attempt finishes on 6.5. Both were reported as symptoms long before
the cause was found, which is why the three properties below are asserted
separately rather than left to a comment.
"""
import inspect
import os

from celery.exceptions import SoftTimeLimitExceeded

import celery_task


class TestTheWorkerOutwaitsTheStream:
    def test_the_task_is_not_killed_before_the_stream_gives_up(self):
        stream_waits = int(os.getenv("MAX_STREAM_SECONDS", "900"))
        assert celery_task.GENERATION_SOFT_LIMIT >= stream_waits, (
            "the worker kills the generation while the browser is still waiting "
            "for it, and the user is told nothing"
        )

    def test_the_hard_kill_leaves_room_to_finish_delivering(self):
        assert celery_task.GENERATION_HARD_LIMIT > celery_task.GENERATION_SOFT_LIMIT

    def test_the_limits_are_what_the_task_actually_runs_with(self):
        """Both were literals in the decorator once, and drifted from the
        stream's own timeout without anything noticing."""
        task = celery_task.generate_content
        assert task.soft_time_limit == celery_task.GENERATION_SOFT_LIMIT
        assert task.time_limit == celery_task.GENERATION_HARD_LIMIT


class TestRunningOutOfTimeIsNotRetried:
    def test_the_timeout_is_caught_before_the_generic_handler(self):
        source = inspect.getsource(celery_task.generate_content)
        assert "except SoftTimeLimitExceeded" in source
        # rindex: an inner handler earlier in the body catches its own failure.
        # The one that retries is the last, and that is the one to get in front of.
        assert source.index("except SoftTimeLimitExceeded") < source.rindex("except Exception"), (
            "a later handler never runs; SoftTimeLimitExceeded is an Exception "
            "and would be retried by the generic one"
        )

    def test_the_timeout_handler_does_not_retry(self):
        source = inspect.getsource(celery_task.generate_content)
        timeout_block = source.split("except SoftTimeLimitExceeded", 1)[1].split("except Exception", 1)[0]
        assert "self.retry" not in timeout_block, (
            "retrying a pipeline that ran out of time spends another full run to "
            "arrive at the same place, racing the attempt already in flight"
        )

    def test_the_run_is_marked_failed_so_the_stream_stops(self):
        source = inspect.getsource(celery_task.generate_content)
        timeout_block = source.split("except SoftTimeLimitExceeded", 1)[1].split("except Exception", 1)[0]
        assert "_mark_generation_failed" in timeout_block, (
            "without a terminal status the endpoint holds the connection open "
            "until MAX_STREAM_SECONDS with nothing coming"
        )

    def test_a_timeout_would_not_be_swallowed_by_the_generic_handler(self):
        """The property in one line, independent of how the source is laid
        out: the exception this task must not retry is an ordinary Exception."""
        assert issubclass(SoftTimeLimitExceeded, Exception)
