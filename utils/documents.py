"""Questions about a brand's uploaded documents."""
import logging

logger = logging.getLogger(__name__)


def has_reference_documents(business_id: str, content_type: str) -> bool:
    """Does this content type have any reference documents indexed?

    Only reference documents (product docs, press kits, briefs) carry internal
    planning material, so the publishable-facts filter is only worth an LLM call
    when at least one exists. Returns False on any error so a database problem
    costs a filter pass, not the generation.
    """
    try:
        from database import BrandDocument, DOC_ROLE_REFERENCE, get_db_session

        with get_db_session() as session:
            return session.query(BrandDocument.id).filter_by(
                business_id=business_id,
                content_type=content_type,
                doc_role=DOC_ROLE_REFERENCE,
            ).first() is not None
    except Exception as e:
        logger.warning("Could not check for reference documents: %s", e)
        return False


def reference_documents(business_id: str, content_type: str) -> str:
    """The product documents themselves, whole, for this content type.

    The story-coverage check needs the document, not what retrieval returned
    from it. Retrieval hands back chunks: headings are separated from the text
    beneath them, or dropped, and what survives is prose with no structure. So
    the checker's rule for skipping a document's planning sections — a heading
    whose words say it is a cast list or a set of notes — has nothing to match,
    and the treatment's own title block ("Film / Limited Series Treatment —
    Based on True Events. Status: Part 1 of an ongoing account.") arrived as a
    paragraph of story to be dramatised. A script was marked down for failing
    to dramatise it in every round of a run.

    Returns "" when there are none, and the caller falls back to the research.
    """
    try:
        from database import BrandDocument, DOC_ROLE_REFERENCE, get_db_session

        with get_db_session() as session:
            rows = session.query(BrandDocument.file_content).filter_by(
                business_id=business_id,
                content_type=content_type,
                doc_role=DOC_ROLE_REFERENCE,
            ).all()
        return "\n\n".join(row[0] for row in rows if row and row[0])
    except Exception as e:
        logger.warning("Could not load reference documents: %s", e)
        return ""


def voice_documents(business_id: str, content_type: str) -> str:
    """The brand's own writing for this content type, whole.

    Used where a habit has to be read off the documents rather than off the
    Brain — scene-heading format, for instance, which is punctuation the brand
    chose and which nothing measures into the spec.
    """
    try:
        from database import BrandDocument, DOC_ROLE_VOICE, get_db_session

        with get_db_session() as session:
            rows = session.query(BrandDocument.file_content).filter_by(
                business_id=business_id,
                content_type=content_type,
                doc_role=DOC_ROLE_VOICE,
            ).all()
        return "\n\n".join(row[0] for row in rows if row and row[0])
    except Exception as e:
        logger.warning("Could not load voice documents: %s", e)
        return ""
