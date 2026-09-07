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
