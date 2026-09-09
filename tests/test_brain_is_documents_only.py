"""
The Brand Brain is derived from uploaded documents and nothing else.

It states how this brand writes. A generation is this system's own output, so
promoting one into the Brain let a single piece redefine the brand, and the
drift compounded because each new generation re-extracted the previous one's
inventions. Observed live on a documentary distributor: a tortoise-and-hare
script taught it that it valued "mindfulness", that its metaphors came from
"classic fables", and that its authority rested on "an old storyteller" rather
than on 11 years and 71 commissions.

Approval still teaches, through the reviewer path into the writer. These tests
guard the boundary: they fail if a route from generated content back into the
Brain is reintroduced.
"""
import inspect

import brand_metrics
import celery_task
import human_loop
from nodes import deployer


class TestNoRouteFromGenerationsIntoTheBrain:
    def test_the_promotion_task_no_longer_exists(self):
        assert not hasattr(celery_task, "promote_generation_feedback")

    def test_the_promotion_task_is_not_registered_with_celery(self):
        assert "tasks.promote_generation_feedback" not in celery_task.celery_app.tasks

    def test_it_has_no_queue_route(self):
        routes = celery_task.celery_app.conf.task_routes or {}
        assert "tasks.promote_generation_feedback" not in routes

    def test_the_metrics_writer_for_generations_is_gone(self):
        assert not hasattr(brand_metrics.BrandMetricsSQL, "save_generation_feedback")

    def test_the_deployer_does_not_promote(self):
        source = inspect.getsource(deployer)
        assert "promote_generation_feedback" not in source

    def test_only_documents_can_write_a_metrics_row(self):
        """
        extract_and_save takes a doc_id. If a second writer of BrandMetrics rows
        appears, this is the test that should make someone justify it.
        """
        writers = [
            name for name, member in inspect.getmembers(
                brand_metrics.BrandMetricsSQL, predicate=inspect.isfunction
            )
            if "BrandMetrics(" in (inspect.getsource(member) or "")
        ]
        assert writers == ["extract_and_save"], (
            f"something other than document extraction writes metric rows: {writers}"
        )


class TestTheReviewerPathSurvives:
    """Deleting the Brain route must not have taken the writer route with it."""

    def test_the_writer_reads_learned_patterns(self):
        from nodes import writer
        assert "memory.get_patterns(" in inspect.getsource(writer)

    def test_the_deployer_still_records_the_generation_for_review(self):
        assert "memory.save(" in inspect.getsource(deployer)

    def test_reviewer_feedback_is_still_persisted(self):
        assert "save_feedback(" in inspect.getsource(celery_task.process_feedback)

    def test_a_rejection_still_re_triggers_generation(self):
        source = inspect.getsource(human_loop.handle_review_outcome)
        assert "generate_content" in source or "regeneration_depth" in source

    def test_the_review_handler_is_not_named_after_a_path_that_no_longer_exists(self):
        assert not hasattr(human_loop, "promote_to_brand_metrics")
        assert hasattr(human_loop, "handle_review_outcome")
