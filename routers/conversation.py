import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from auth import get_current_user, require_business_access
from limiter import limiter
from schema import GenerateRequest, FeedbackRequest, TaskResponse
from utils import logger

router = APIRouter()


@router.post("/generate/stream")
@limiter.limit("60/minute")
async def generate_stream(
    request: Request,
    body: GenerateRequest,
    current_user: Annotated[object, Depends(get_current_user)],
):
    from celery_task import generate_content
    from database import get_db_session, Generation, User
    from celery_task import get_async_redis

    require_business_access(current_user, body.business_id)


    # ── Daily quota enforcement (Redis counter, resets at midnight UTC) ─────
    async_redis = get_async_redis()

    try:
        # Quota is always charged to the authenticated user, never to a
        # user_id supplied by the client.
        user = current_user

        if user:
            now = datetime.now(timezone.utc)
            today_str = now.strftime("%Y-%m-%d")
            redis_key = f"daily_gen:{user.id}:{today_str}"

            # Atomically increment; returns new value
            daily_used = await async_redis.incr(redis_key)

            # Set TTL to expire exactly at the next midnight UTC (only on first use)
            if daily_used == 1:
                seconds_until_midnight = (
                    86400
                    - now.hour * 3600
                    - now.minute * 60
                    - now.second
                )
                await async_redis.expire(redis_key, seconds_until_midnight)

            if daily_used > user.daily_generation_limit:
                # Roll back the increment so it doesn't inflate future checks
                await async_redis.decr(redis_key)

                tomorrow = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
                tomorrow += timedelta(days=1)

                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "QUOTA_EXHAUSTED",
                        "message": "Daily generation limit reached.",
                        "limit": user.daily_generation_limit,
                        "used": int(daily_used) - 1,
                        "reset_date": f"{tomorrow.strftime('%B')} {tomorrow.day}, {tomorrow.year}",
                        "reset_iso": tomorrow.isoformat(),
                    }
                )
    finally:
        await async_redis.aclose()
    # ─────────────────────────────────────────────────────────────────────────

    generation_id = str(uuid4())

    generate_content.delay(
        generation_id=generation_id,
        business_id=body.business_id,
        content_type=body.content_type,
        topic=body.topic,
        format_type=body.format_type,
        user_id=current_user.id,
        use_search=body.use_search,
        research_mode=body.research_mode,
    )

    async def stream():
        yield json.dumps({"generation_id": generation_id}) + "\n"

        from celery_task import get_async_redis
        from redis.exceptions import (
            ConnectionError as RedisConnectionError,
            TimeoutError as RedisTimeoutError,
        )

        stream_redis = get_async_redis()
        start = time.monotonic()

        # An enforcer iteration is a full LLM round trip, and the press-release
        # path can run several. 300s cut the stream off mid-pipeline, so the
        # client saw the researcher's output and then a clean end of response.
        MAX_STREAM_SECONDS = int(os.getenv("MAX_STREAM_SECONDS", "900"))
        BLPOP_SECONDS = 5

        try:
            while True:
                if await request.is_disconnected():
                    break
                if time.monotonic() - start > MAX_STREAM_SECONDS:
                    logger.warning("Stream timed out generation_id=%s", generation_id)
                    break

                try:
                    chunk = await stream_redis.blpop(
                        f"stream:{generation_id}", timeout=BLPOP_SECONDS
                    )  # type: ignore[misc]
                except (RedisTimeoutError, RedisConnectionError) as e:
                    # Not an error condition. No node has published yet, which
                    # is the normal state while a writer or enforcer LLM call is
                    # in flight — those take far longer than one BLPOP window.
                    # Uncaught, this propagated out of the generator and ended
                    # the response, so every generation appeared to stop after
                    # the researcher.
                    logger.debug(
                        "No stream data yet for generation_id=%s (%s)",
                        generation_id, e.__class__.__name__,
                    )
                    chunk = None

                if chunk is None:
                    with get_db_session() as session:
                        record = session.get(Generation, generation_id)
                        if record and record.status in ("completed", "failed", "timeout"):
                            break

                    # Keeps the connection demonstrably alive across a long node.
                    # Without it a reverse proxy can drop an apparently idle
                    # response, and a watching user cannot tell a slow enforcer
                    # from a hung one.
                    yield json.dumps(
                        {"heartbeat": round(time.monotonic() - start)}
                    ) + "\n"
                    continue

                yield chunk[1] + "\n"
        finally:
            await stream_redis.aclose()

    return StreamingResponse(stream(), media_type="application/json")


@router.post("/feedback", response_model=TaskResponse)
@limiter.limit("30/minute")
async def submit_feedback(
    request: Request,
    feedback: FeedbackRequest,
    current_user: Annotated[object, Depends(get_current_user)],
):
    from celery_task import process_feedback

    require_business_access(current_user, feedback.business_id)

    task = process_feedback.delay(
        generation_id=feedback.generation_id,
        business_id=feedback.business_id,
        content_type=feedback.content_type,
        human_approved=feedback.human_approved,
        human_score=feedback.human_score,
        human_feedback=feedback.human_feedback
    )

    logger.info(
        "Feedback queued task_id=%s generation_id=%s approved=%s",
        task.id, feedback.generation_id, feedback.human_approved
    )

    return {"generation_id": feedback.generation_id, "task_id": task.id, "status": "queued"}


@router.get("/health")
async def health():
    return {"status": "healthy"}


@router.get("/patterns/{business_id}/{content_type}")
async def get_patterns(
    business_id: str,
    content_type: str,
    current_user: Annotated[object, Depends(get_current_user)],
):
    from learning_memory import FeedbackPortSQL

    require_business_access(current_user, business_id)

    memory = FeedbackPortSQL(business_id=business_id)
    patterns = memory.get_patterns(content_type)

    return {
        "business_id": business_id,
        "content_type": content_type,
        "approved_count": len(patterns["approved"]),
        "rejected_count": len(patterns["rejected"]),
        "patterns": patterns
    }