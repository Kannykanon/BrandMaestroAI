"""
Two things a user has to be able to trust about an uploaded document: that the
UI tells them when it has been processed, and that deleting it deletes it.

Both were broken in the same way — the code that should have written the outcome
was simply absent, so the default persisted and looked like a real state.
"""
import inspect
import re

import celery_task
from routers import document as document_router


class TestStatusReachesATerminalState:
    """
    BrandDocument.status defaults to 'pending' and nothing ever moved it, so
    every document showed as pending forever — including the five already
    extracted into a working Brand Brain.
    """

    def test_extract_metrics_marks_success(self):
        source = inspect.getsource(celery_task.extract_metrics)
        assert "_set_document_status(doc_id, DOC_STATUS_COMPLETED)" in source

    def test_extract_metrics_marks_an_already_extracted_document_complete(self):
        """Already in the Brain is what the status reports, so it is not pending."""
        source = inspect.getsource(celery_task.extract_metrics)
        already = source.split("if not inserted:")[1].split("return")[0]
        assert "DOC_STATUS_COMPLETED" in already

    def test_extract_metrics_marks_terminal_failure(self):
        source = inspect.getsource(celery_task.extract_metrics)
        assert "DOC_STATUS_FAILED" in source
        assert "_retries_exhausted(self)" in source, (
            "a document must not show as failed while a retry is still pending"
        )

    def test_reference_documents_can_also_reach_a_terminal_state(self):
        """
        A reference document is processed by refresh_rag, not extract_metrics, so
        without a doc_id there it had no path to any status at all.
        """
        params = inspect.signature(celery_task.refresh_rag).parameters
        assert "doc_id" in params
        assert params["doc_id"].default is None, "must stay optional for the rebuild path"
        source = inspect.getsource(celery_task.refresh_rag)
        assert "DOC_STATUS_COMPLETED" in source
        assert "DOC_STATUS_FAILED" in source

    def test_the_upload_route_passes_doc_id_to_both_tasks(self):
        source = inspect.getsource(document_router)
        refresh_call = source.split("refresh_rag.s(")[1].split(")")[0]
        assert "doc_id=doc_id" in refresh_call, "reference uploads would stay pending"

    def test_marking_status_never_raises(self):
        """Bookkeeping must not turn a processed document into a failed one."""
        celery_task._set_document_status(None, celery_task.DOC_STATUS_COMPLETED)
        celery_task._set_document_status(-12345, celery_task.DOC_STATUS_COMPLETED)

    def test_the_list_endpoint_exposes_the_reason_for_a_failure(self):
        source = inspect.getsource(document_router)
        assert '"error_message": d.error_message' in source


class TestDeletingDocumentsDeletesTheBrain:
    def test_the_last_voice_document_takes_the_brain_with_it(self):
        source = inspect.getsource(document_router.delete_document)
        assert "delete_all()" in source
        assert "brand_brain_deleted" in source

    def test_a_remaining_document_triggers_a_rebuild_instead(self):
        source = inspect.getsource(document_router.delete_document)
        assert "synthesize_metrics.delay(" in source

    def test_it_no_longer_calls_extraction_with_no_document(self):
        """
        The old rebuild was extract_metrics(doc_id=None, doc_content=None), and
        extract_and_save hashes doc_content on its first line — so the task threw
        AttributeError on None, retried three times and died. Deleting a voice
        document never re-synthesised anything.
        """
        source = inspect.getsource(document_router.delete_document)
        assert not re.search(r"extract_metrics\.delay\(", source)

    def test_a_brain_cannot_outlive_the_rows_it_came_from(self):
        """
        The invariant that makes every delete path safe, wherever the last row
        goes: synthesis with nothing to synthesise from removes the stored brain
        rather than leaving one standing with no corpus underneath it.
        """
        import brand_metrics
        source = inspect.getsource(brand_metrics.BrandMetricsSQL.build_and_cache_context)
        empty_branch = source.split("if not rows:")[1].split("return")[0]
        assert "BrandBrain" in empty_branch and "delete()" in empty_branch
        assert "invalidate_cache" in empty_branch

    def test_the_bulk_reset_still_clears_everything(self):
        source = inspect.getsource(document_router.reset_brand_brain)
        assert "delete_all()" in source
        assert "_purge_vectors(" in source
