import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
from graph.state import GraphState
from database import get_db_session, Generation
from utils.observe import observe

PROMOTION_THRESHOLD = 8.5


@observe("deployer_node")
def deployer_node(state: GraphState) -> GraphState:
    from graph.deps import resolve_deps
    _rag, _analyzer, memory = resolve_deps(state["business_id"], state["content_type"])

    generation_id = state["generation_id"]
    score = state.get("score", 0.0)
    logger.info("DEPLOYER NODE: received content of length %s", len(state.get("content", "")))

    # Persist to DB
    with get_db_session() as session:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        completed_at = datetime.now(timezone.utc)
        stmt = pg_insert(Generation).values(
            generation_id=generation_id,
            business_id=state["business_id"],
            content_type=state["content_type"],
            topic=state["topic"],
            format_type=state["format_type"],
            user_id=state.get("user_id"),
            status="completed",
            score=score,
            content=state["content"],
            completed_at=completed_at
        ).on_conflict_do_update(
            index_elements=["generation_id"],
            set_={
                "status": "completed",
                "score": score,
                "content": state["content"],
                "completed_at": completed_at
            }
        )
        session.execute(stmt)
        session.commit()

    # Save to learning memory
    memory.save(
        generation_id=generation_id,
        content_type=state["content_type"],
        creative_angle=state.get("creative_angle", "unknown"),
        generated_content=state["content"],
        auto_score=score,
        topic=state["topic"],
        format_type=state["format_type"],
        user_id=state.get("user_id"),
        style_match=state.get("style_match", 0.0),
        tone_match=state.get("tone_match", 0.0),
        structure_match=state.get("structure_match", 0.0),
        signature_match=state.get("signature_match", 0.0),
        # Recorded so a later rejection can re-run this generation the same way
        # it was originally run, and so the reject -> regenerate loop is bounded.
        use_search=bool(state.get("use_search", False)),
        regeneration_depth=int(state.get("regeneration_depth") or 0)
    )

    # Notify user for review if webhook_url provided
    webhook_url = state.get("webhook_url")
    if webhook_url:
        try:
            from celery_task import send_notification
            send_notification.delay(
                webhook_url=webhook_url,
                content=state["content"],
                generation_id=generation_id,
                topic=state["topic"]
            )
            logger.info("Notification queued for generation_id=%s", generation_id)
        except Exception as e:
            logger.warning("Failed to queue notification for generation_id=%s: %s", generation_id, e)

    # Promote high-scoring generations to brand_metrics to improve future generations
    if score >= PROMOTION_THRESHOLD:
        try:
            from celery_task import promote_generation_feedback
            promote_generation_feedback.delay(
                business_id=state["business_id"],
                content_type=state["content_type"],
                generation_content=state["content"],
                human_approved=False,
                score=score
            )
            logger.info(
                "Promoted generation to brand_metrics generation_id=%s score=%.1f",
                generation_id, score
            )
        except Exception as e:
            logger.warning(
                "Failed to promote generation generation_id=%s: %s", generation_id, e
            )

    logger.info(
        "Generation complete generation_id=%s score=%.1f iterations=%d",
        generation_id, score, state.get("iteration", 1)
    )

    return {
        **state,
        "status": "completed"
    }