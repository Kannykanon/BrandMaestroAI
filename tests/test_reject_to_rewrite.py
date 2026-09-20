"""A reviewer rejects a draft with a reason, and a rewrite is queued.

Reported as not working for weeks, and every layer said it was: the endpoint
returned 200, the task returned "saved", and the page said the rewrite was on
its way. What actually happened was that save_feedback looked for a
reviewer_learning row, did not find one, and returned — no exception, no log,
no record. The rewrite step then looked for a rejection, found none, and
queued nothing.

The row was missing because it is written by the deployer, the last node in
the graph. Any run that ended before it — and until recently the worker killed
every run at 300 seconds — leaves a generation the reviewer can see and open,
and nothing to attach a verdict to.

The tests that were supposed to cover this asserted that the string
"save_feedback(" appears in the task's source. It does, and it did throughout.
"""
import pytest

import celery_task
import human_loop
import learning_memory
from database import Generation, ReviewerLearning


class _Query:
    def __init__(self, rows, model):
        self.rows = [r for r in rows if isinstance(r, model)]

    def filter_by(self, **terms):
        self.rows = [r for r in self.rows
                     if all(getattr(r, k, None) == v for k, v in terms.items())]
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    def query(self, model):
        return _Query(self.rows, model)

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1


@pytest.fixture
def db(monkeypatch):
    rows = []

    class _Ctx:
        def __enter__(self):
            return _Session(rows)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(learning_memory, "get_db_session", lambda: _Ctx())
    monkeypatch.setattr(human_loop, "get_db_session", lambda: _Ctx())
    return rows


@pytest.fixture
def queued(monkeypatch):
    calls = []

    class _Task:
        def delay(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(celery_task, "generate_content", _Task())
    return calls


def _generation(**over):
    fields = dict(generation_id="g1", business_id="b1", content_type="script",
                  topic="Kancity", format_type="video script", user_id=7,
                  status="completed", score=6.5, content="FADE IN.", approved=False,
                  parent_generation_id=None)
    fields.update(over)
    return Generation(**fields)


def _reviewed(**over):
    fields = dict(generation_id="g1", business_id="b1", content_type="script",
                  topic="Kancity", format_type="video script", user_id=7,
                  creative_angle="unknown", generated_content="FADE IN.",
                  agent_auto_score=6.5, agent_auto_approved=False,
                  has_human_feedback=False, use_search=False, regeneration_depth=0)
    fields.update(over)
    return ReviewerLearning(**fields)


class TestFeedbackIsNeverSilentlyDiscarded:
    def test_a_generation_that_never_reached_the_deployer_still_takes_feedback(self, db):
        db.append(_generation())
        port = learning_memory.FeedbackPortSQL(business_id="b1")

        assert port.save_feedback("g1", False, 3.0, "The ending is invented.") is True

        learned = [r for r in db if isinstance(r, ReviewerLearning)]
        assert len(learned) == 1
        assert learned[0].has_human_feedback is True
        assert learned[0].human_approved is False
        assert learned[0].human_feedback == "The ending is invented."

    def test_the_backfilled_row_carries_what_the_rewrite_needs(self, db):
        db.append(_generation())
        learning_memory.FeedbackPortSQL(business_id="b1").save_feedback("g1", False, 3.0, "no")

        row = next(r for r in db if isinstance(r, ReviewerLearning))
        assert row.topic == "Kancity" and row.format_type == "video script"
        assert row.content_type == "script" and row.user_id == 7

    def test_an_existing_row_is_updated_not_duplicated(self, db):
        db.extend([_generation(), _reviewed()])
        learning_memory.FeedbackPortSQL(business_id="b1").save_feedback("g1", False, 3.0, "no")

        assert len([r for r in db if isinstance(r, ReviewerLearning)]) == 1

    def test_a_generation_that_does_not_exist_reports_failure(self, db):
        """The only case left where nothing can be recorded. It now says so
        instead of returning as though it had worked."""
        assert learning_memory.FeedbackPortSQL(business_id="b1").save_feedback(
            "nope", False, 3.0, "no") is False

    def test_depth_survives_a_gap_in_the_chain(self, db):
        """A backfilled row must not reset the reject -> rewrite cap to zero,
        or a reviewer who keeps rejecting spawns generations forever."""
        db.extend([_reviewed(generation_id="g0", regeneration_depth=2),
                   _generation(generation_id="g1", parent_generation_id="g0")])
        learning_memory.FeedbackPortSQL(business_id="b1").save_feedback("g1", False, 3.0, "no")

        row = next(r for r in db if isinstance(r, ReviewerLearning) and r.generation_id == "g1")
        assert row.regeneration_depth == 3


class TestTheTaskDoesNotClaimSuccessItDidNotHave:
    def test_it_reports_the_generation_was_not_found(self):
        import inspect

        source = inspect.getsource(celery_task.process_feedback)
        assert "if not saved" in source
        assert "FEEDBACK NOT SAVED" in source


class TestARejectionQueuesTheRewrite:
    def test_a_rejection_queues_one(self, db, queued):
        db.append(_reviewed(has_human_feedback=True, human_approved=False,
                            human_feedback="The ending is invented."))
        human_loop.handle_review_outcome("g1")

        assert len(queued) == 1
        assert queued[0]["human_feedback"] == "The ending is invented."
        assert queued[0]["parent_generation_id"] == "g1"
        assert queued[0]["regeneration_depth"] == 1

    def test_an_approval_queues_nothing(self, db, queued):
        db.append(_reviewed(has_human_feedback=True, human_approved=True))
        human_loop.handle_review_outcome("g1")
        assert queued == []

    def test_the_cap_still_holds(self, db, queued):
        db.append(_reviewed(has_human_feedback=True, human_approved=False,
                            regeneration_depth=human_loop.MAX_REGENERATION_DEPTH))
        human_loop.handle_review_outcome("g1")
        assert queued == []

    @pytest.mark.parametrize("use_search,expected", [(False, "rag"), (True, "both")])
    def test_the_rewrite_keeps_the_sources_the_original_used(self, db, queued,
                                                             use_search, expected):
        """use_search was read from the record and handed over, and then
        overruled: generate_content defaults research_mode to "both", and the
        researcher only falls back to use_search when research_mode is absent.
        A piece grounded in the brand's own documents was rewritten with web
        search on."""
        db.append(_reviewed(has_human_feedback=True, human_approved=False,
                            use_search=use_search))
        human_loop.handle_review_outcome("g1")

        assert queued[0]["research_mode"] == expected
        assert queued[0]["use_search"] is use_search
