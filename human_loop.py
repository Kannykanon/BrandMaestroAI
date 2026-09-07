import logging
import uuid
from sqlalchemy.exc import SQLAlchemyError
from database import get_db_session, ReviewerLearning

logger = logging.getLogger(__name__)

# Ceiling on reject -> regenerate hops for a single piece of content. The
# writer/enforcer loop inside the graph is already bounded at 3; without this
# the outer human loop was unbounded, so a reviewer who kept rejecting would
# keep spawning generations forever.
MAX_REGENERATION_DEPTH = 3


def promote_to_brand_metrics(generation_id: str) -> None:
    """
    Called by process_feedback after human feedback is saved.
    - Approved: no action needed — generation already saved to DB
    - Rejected: re-trigger full generation with human feedback injected,
      preserving the original research mode and bounding the loop depth
    """
    from celery_task import generate_content

    try:
        with get_db_session() as session:
            record = session.query(ReviewerLearning).filter_by(
                generation_id=generation_id,
                has_human_feedback=True
            ).first()

            if not record:
                logger.warning("No feedback found for generation_id=%s", generation_id)
                return

            if record.human_approved:
                logger.info("Generation %s approved — no re-trigger needed", generation_id)
                return

            # Read every attribute needed after the session closes, while the
            # instance is still attached.
            next_depth = (record.regeneration_depth or 0) + 1
            business_id = record.business_id
            content_type = record.content_type
            topic = record.topic
            format_type = record.format_type
            user_id = record.user_id
            human_feedback = record.human_feedback
            use_search = bool(record.use_search)

        if next_depth > MAX_REGENERATION_DEPTH:
            logger.warning(
                "Regeneration cap reached for generation_id=%s (depth=%d, max=%d) — "
                "not re-triggering. This content needs a human rewrite or a new brief.",
                generation_id, next_depth, MAX_REGENERATION_DEPTH,
            )
            return

        # Rejected — re-trigger with human feedback injected. use_search comes
        # from the original request rather than being hardcoded, so a draft that
        # was grounded in uploaded documents is not silently switched to web
        # search (or vice versa) just because a human asked for an edit.
        generate_content.delay(
            generation_id=str(uuid.uuid4()),
            business_id=business_id,
            content_type=content_type,
            topic=topic,
            format_type=format_type,
            user_id=user_id,
            use_search=use_search,
            human_feedback=human_feedback,
            regeneration_depth=next_depth,
        )
        logger.info(
            "Re-generation triggered from rejected generation_id=%s (depth=%d, use_search=%s)",
            generation_id, next_depth, use_search,
        )

    except SQLAlchemyError as e:
        logger.error("DB error in promote_to_brand_metrics generation_id=%s: %s", generation_id, e)
