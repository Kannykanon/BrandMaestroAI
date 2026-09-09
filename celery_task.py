import json
import logging
import os

import redis
from celery import Celery
from kombu import Exchange, Queue

from database import get_db_session
# Imported at module level (worker boot time) rather than inside
# _run_graph(), since importing opik itself costs ~8-10s — that must land on
# worker startup, not on whichever live generation request runs first.
from observability import get_trace_callbacks

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------

# Redis is the default broker rather than RabbitMQ. Redis is already required
# for the Brand Brain cache, the result backend and the generation stream, so
# brokering on it too removes an entire service — which matters wherever
# managed RabbitMQ is not on offer (Render, Fly, most PaaS free tiers).
#
# RabbitMQ is still supported: set CELERY_BROKER_URL (or the legacy RABBIT_URL)
# to an amqp:// URL and Celery uses it unchanged. The queue and routing config
# below is transport-agnostic.
# RABBIT_URL is deliberately NOT consulted: docker-compose no longer starts a
# RabbitMQ service, so honouring a stale RABBIT_URL left in someone's .env
# would point the broker at a host that is not running. Set CELERY_BROKER_URL
# explicitly to use RabbitMQ.
CELERY_BROKER_URL = (
    os.getenv("CELERY_BROKER_URL")
    or os.getenv("REDIS_URL", "redis://redis:6379/0")
)

celery_app = Celery(
    "brandguard",
    broker=CELERY_BROKER_URL,
    backend=os.getenv("REDIS_URL", "redis://redis:6379/0")
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True, 
    task_reject_on_worker_lost=True,
    task_queues=[
        Queue("generation",  durable=True, exchange=Exchange("generation",  type="direct"), routing_key="generation"),
        Queue("feedback",    durable=True, exchange=Exchange("feedback",    type="direct"), routing_key="feedback"),
        Queue("retraining",  durable=True, exchange=Exchange("retraining",  type="direct"), routing_key="retraining"),
        Queue("rag_refresh", durable=True, exchange=Exchange("rag_refresh", type="direct"), routing_key="rag_refresh"), 
    ],
    task_default_queue="generation",
    task_routes={
        "tasks.generate_content":            {"queue": "generation"},
        "tasks.process_feedback":            {"queue": "feedback"},
        "tasks.retrain":                     {"queue": "retraining"},
        "tasks.refresh_rag":                 {"queue": "rag_refresh"},
        "tasks.extract_metrics":             {"queue": "rag_refresh"},  
        "tasks.synthesize_metrics":          {"queue": "rag_refresh"},
    }
)

def _run_pipeline_remote(resource_name: str, initial_state: dict) -> dict:
    """Runs the pipeline via the agent deployed on Agent Platform Runtime
    (see deploy/deploy_agent.py). This is the path actually exercised in
    production/demo — the Researcher -> Writer -> Enforcer -> Deployer
    loop executes on Google Cloud, not in this worker process."""
    import vertexai
    from vertexai import agent_engines

    vertexai.init(
        project=os.environ["PROJECT_ID"],
        location=os.getenv("LOCATION", "us-central1"),
    )
    remote_app = agent_engines.get(resource_name)
    return remote_app.query(input=initial_state)


def _run_pipeline(initial_state: dict) -> dict:
    """
    Routes a generation request to Agent Platform Runtime when
    AGENT_ENGINE_RESOURCE_NAME is set (production/demo path); otherwise
    falls back to building and running the LangGraph pipeline in-process
    (local dev / offline tests, no GCP credentials required).
    """
    resource_name = os.getenv("AGENT_ENGINE_RESOURCE_NAME")
    if resource_name:
        logger.info("Routing generation through Agent Platform Runtime: %s", resource_name)
        return _run_pipeline_remote(resource_name, initial_state)

    logger.info("AGENT_ENGINE_RESOURCE_NAME not set - running graph in-process")
    import asyncio

    from graph.graph import build_graph
    from search import ParallelSearch

    # Default to "" so a missing key raises ParallelSearch's ValueError with a
    # readable message, instead of AttributeError on None.strip().
    search = ParallelSearch(api_key=os.getenv("PARALLEL_API_KEY", ""))
    graph_flow = build_graph(search)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run_graph(graph_flow, initial_state))
    finally:
        loop.close()


def _release_lock(lock, lock_key: str) -> None:
    """Release a Redis lock without masking the original error.

    redis-py raises LockNotOwnedError if the lock's TTL expired while the task
    was still running. Raised from a finally block, that would replace the real
    exception with a misleading one.
    """
    try:
        lock.release()
    except Exception as e:
        logger.warning("Could not release lock %s: %s", lock_key, e)


def _mark_generation_failed(
    generation_id: str,
    business_id: str,
    content_type: str,
    topic: str,
    format_type: str,
    user_id: int = None,
) -> None:
    """Write a terminal 'failed' status for a generation.

    deployer_node only ever writes status='completed', so a pipeline that dies
    before reaching it leaves no terminal row. Clients polling for completion
    need this to stop waiting. Never raises - it runs inside an except block.
    """
    from datetime import datetime, timezone

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from database import Generation

    try:
        with get_db_session() as session:
            stmt = pg_insert(Generation).values(
                generation_id=generation_id,
                business_id=business_id,
                content_type=content_type,
                topic=topic,
                format_type=format_type,
                user_id=user_id,
                status="failed",
                completed_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=["generation_id"],
                set_={
                    "status": "failed",
                    "completed_at": datetime.now(timezone.utc),
                },
            )
            session.execute(stmt)
            session.commit()
        logger.info("Marked generation_id=%s as failed", generation_id)
    except Exception as e:
        logger.error(
            "Could not mark generation_id=%s as failed: %s", generation_id, e
        )


def get_redis():
    return redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True,
    )


def get_async_redis():
    from redis.asyncio import Redis
    return Redis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True,
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="tasks.generate_content",
    max_retries=2,              
    default_retry_delay=10,
    soft_time_limit=300,  
    time_limit=360         
)
def generate_content(
    self,
    generation_id: str,
    business_id: str,
    content_type: str,
    topic: str,
    format_type: str,
    user_id: int = None,
    use_search: bool = False,
    human_feedback: str = "",
    regeneration_depth: int = 0
):
    from database import Generation
    from graph.state import GraphState

    try:
        initial_state = GraphState(
            business_id=business_id,
            content_type=content_type,
            topic=topic,
            format_type=format_type,
            user_id=user_id,
            use_search=use_search,
            human_feedback=human_feedback,
            regeneration_depth=regeneration_depth,
            research="",
            content="",
            creative_angle="",
            iteration=0,
            approved=False,
            feedback="",
            flagged_passages="",
            score=0.0,
            generation_id=generation_id,
            status="pending"
        )

        with get_db_session() as session:
            existing = session.get(Generation, generation_id)
            if existing and existing.status == "completed":
                logger.info(
                    "Generation already completed business_id=%s generation_id=%s",
                    business_id, generation_id
                )
                return {"status": "completed", "generation_id": generation_id}

        # Token accounting is per-generation, so start from zero and report what
        # this run actually cost once it finishes. Captured at the Gemini SDK
        # boundary (see model.install_token_capture) because the pinned
        # langchain-google-genai drops usage before any callback sees it.
        from model import TokenUsage
        TokenUsage.reset()

        final_state = _run_pipeline(initial_state)

        try:
            from observability import log_token_usage_to_opik
            usage = log_token_usage_to_opik(
                label=f"{content_type}:{generation_id}",
                extra={"iterations": final_state.get("iteration"),
                       "approved": final_state.get("approved"),
                       "score": final_state.get("score")},
            )
            logger.info(
                "Generation %s token usage: calls=%s prompt=%s output=%s cached=%s",
                generation_id, usage["calls"], usage["prompt_tokens"],
                usage["output_tokens"], usage["cached_tokens"],
            )
        except Exception as e:
            logger.debug("Token usage reporting skipped: %s", e)

        # _run_graph swallows graph exceptions and flags the state instead of
        # raising, so the deployer never ran and no terminal row exists.
        if final_state.get("status") == "failed":
            _mark_generation_failed(
                generation_id=generation_id,
                business_id=business_id,
                content_type=content_type,
                topic=topic,
                format_type=format_type,
                user_id=user_id,
            )
            return {"status": "failed", "generation_id": generation_id}

        logger.info(
            "Generation complete business_id=%s generation_id=%s score=%.1f",
            business_id,
            final_state.get("generation_id", ""),
            final_state.get("score", 0.0)
        )

        return {
            "status":        "completed",
            "generation_id": generation_id,
            "score":         final_state.get("score", 0.0)
        }

    except Exception as exc:
        logger.error(
            "Generation failed business_id=%s generation_id=%s: %s",
            business_id, generation_id, exc
        )
        try:
            raise self.retry(exc=exc, countdown=10)
        except self.MaxRetriesExceededError:
            # No retries left. The /generate/stream endpoint polls this row to
            # decide when to stop streaming; without a terminal status it would
            # hold the connection open until MAX_STREAM_SECONDS.
            _mark_generation_failed(
                generation_id=generation_id,
                business_id=business_id,
                content_type=content_type,
                topic=topic,
                format_type=format_type,
                user_id=user_id,
            )
            raise


async def _run_graph(graph_flow, initial_state):
    import asyncio

    async_redis = get_async_redis()

    final_state = dict(initial_state)
    generation_id = initial_state['generation_id']
    business_id = initial_state['business_id']
    content_type = initial_state['content_type']
    stream_failed = False

    debounce_key = f"debounce_synthesis:{business_id}:{content_type}"
    try:
        while await async_redis.exists(debounce_key):
            logger.info("Documents are processing. Pausing generation for %s...", business_id)
            await asyncio.sleep(5)

        trace_callbacks = get_trace_callbacks(
            graph_flow,
            tags=[content_type, "web_search" if initial_state.get("use_search") else "rag"],
        )

        async for chunk in graph_flow.astream(initial_state, config={"callbacks": trace_callbacks}):
            for node_name, node_state in chunk.items():
                if isinstance(node_state, dict):
                    final_state.update(node_state)

            if stream_failed:
                continue

            try:
                async with async_redis.pipeline() as pipe:
                    pipe.rpush(f"stream:{generation_id}", json.dumps(chunk))
                    pipe.expire(f"stream:{generation_id}", 3600)
                    await pipe.execute()
            except Exception as e:
                logger.warning("Stream failed for %s: %s", generation_id, e)
                stream_failed = True
                # Continue processing, just stop streaming
                # Client will poll DB for final result
    except Exception as e:
        logger.error("Graph execution failed: %s", e)
        # Still save to DB what we got
        final_state["status"] = "failed"
    finally:
        await async_redis.aclose()

    return final_state


@celery_app.task(
    bind=True,
    name="tasks.process_feedback",
    max_retries=3,
    default_retry_delay=5
)
def process_feedback(
    self,
    generation_id: str,
    business_id: str,
    content_type: str,
    human_approved: bool,
    human_score: float,
    human_feedback: str
) -> dict:
    try:
        from learning_memory import FeedbackPortSQL
        from database import get_db_session, ReviewerLearning

        memory = FeedbackPortSQL(business_id=business_id)

        with get_db_session() as session:
            existing = session.query(ReviewerLearning).filter_by(
                generation_id=generation_id,
                has_human_feedback=True
            ).first()

            if existing:
                logger.info(f"Feedback for {generation_id} already saved, skipping")
                return {"status": "already_saved", "generation_id": generation_id}

        memory.save_feedback(
            generation_id=generation_id,
            human_approved=human_approved,
            human_score=human_score,
            human_feedback=human_feedback
        )

        RETRAIN_THRESHOLD = 100

        with get_db_session() as session:
            # No FOR UPDATE here: PostgreSQL rejects it alongside an aggregate
            # ("FOR UPDATE is not allowed with aggregate functions"). The
            # UPDATE below is atomic on its own, and its rowcount tells us
            # whether this worker or a concurrent one claimed the batch.
            unprocessed = session.query(ReviewerLearning).filter_by(
                business_id=business_id,
                content_type=content_type,
                has_human_feedback=True,
                used_for_retraining=False
            ).count()

            if unprocessed >= RETRAIN_THRESHOLD:
                claimed = session.query(ReviewerLearning).filter_by(
                    business_id=business_id,
                    content_type=content_type,
                    has_human_feedback=True,
                    used_for_retraining=False
                ).update({"used_for_retraining": True}, synchronize_session=False)
                session.commit()

                if claimed:
                    retrain.delay(
                        business_id=business_id,
                        content_type=content_type
                    )
                    logger.info(
                        "Retraining triggered business_id=%s claimed=%d",
                        business_id, claimed
                    )
        
        from human_loop import handle_review_outcome
        handle_review_outcome(generation_id)

        return {"status": "saved", "generation_id": generation_id}

    except Exception as exc:
        logger.error("Feedback failed generation_id=%s: %s", generation_id, exc)
        raise self.retry(exc=exc, countdown=5)


@celery_app.task(
    bind=True,
    name="tasks.retrain",
    max_retries=2,
    default_retry_delay=60
)
def retrain(self, business_id, content_type):
    lock_key = f"retrain:{business_id}:{content_type}"
    r = get_redis()
    lock = r.lock(lock_key, timeout=300)

    if not lock.acquire(blocking=False):
        logger.info(f"Retraining already running for {business_id}, skipping")
        return {"status": "skipped", "reason": "already_running"}

    try:
        from brand_metrics import BrandMetricsSQL

        analyzer = BrandMetricsSQL(
                        business_id=business_id,
                        content_type=content_type
                    )
        analyzer.invalidate_cache()
        analyzer.build_and_cache_context()

        return {"status": "retrained", "business_id": business_id}

    except Exception as exc:
        logger.error("Retraining failed business_id=%s: %s", business_id, exc)
        raise self.retry(exc=exc, countdown=60)

    finally:
        _release_lock(lock, lock_key)


_WEBHOOK_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "[::1]",
    "metadata.google.internal", "169.254.169.254",
}

def _validate_webhook_url(url: str) -> str:
    """Validate webhook URL to prevent SSRF attacks."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("https",):
        raise ValueError(f"Webhook URL must use HTTPS, got '{parsed.scheme}'")
    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname in _WEBHOOK_BLOCKED_HOSTS:
        raise ValueError(f"Webhook hostname '{hostname}' is not allowed")
    if hostname.startswith("10.") or hostname.startswith("192.168.") or hostname.startswith("172."):
        raise ValueError(f"Webhook URL must not target private IP ranges")
    return url


@celery_app.task(bind=True, name="tasks.send_notification", max_retries=3, default_retry_delay=10)
def send_notification(self, webhook_url: str, content: str, generation_id: str, topic: str):
    import httpx
    try:
        webhook_url = _validate_webhook_url(webhook_url)
    except ValueError as e:
        logger.error("Invalid webhook URL for generation_id=%s: %s", generation_id, e)
        return False

    payload = {
        "generation_id": generation_id,
        "topic": topic,
        "message": "Your content is ready for review.",
        "preview": content[:300]
    }
    try:
        with httpx.Client() as client:
            response = client.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Notification sent for generation_id=%s", generation_id)
            return True
    except (httpx.HTTPError, httpx.RequestError) as e:
        logger.error("Notification failed for generation_id=%s: %s", generation_id, e)
        raise self.retry(exc=e, countdown=10)
    


@celery_app.task(
    bind=True,
    name="tasks.refresh_rag",
    max_retries=3,
    default_retry_delay=10
)
def refresh_rag(self, business_id, content_type, new_doc_content):
    import hashlib
    from brand_rag import BrandRAG
    from embedding_stategy import build_embedding
    from brand_metrics import BrandMetricsSQL

    lock = None
    try:
        # None means full rebuild (document deletion path) — no hash needed
        if new_doc_content is not None:
            doc_hash = hashlib.sha256(new_doc_content.encode()).hexdigest()
            lock_key = f"rag_refresh:{business_id}:{content_type}:{doc_hash}"
            lock = get_redis().lock(lock_key, timeout=120)

            if not lock.acquire(blocking=False):
                logger.info("RAG refresh already running for this document")
                return {"status": "skipped", "reason": "already_running"}

        rag = BrandRAG(
            business_id=business_id,
            content_type=content_type,
            embedding=build_embedding()
        )
        analyzer = BrandMetricsSQL(
            business_id=business_id,
            content_type=content_type
        )
        rag.refresh(new_doc_content)
        analyzer.invalidate_cache()
        logger.info(
            "RAG refresh complete business_id=%s content_type=%s",
            business_id, content_type
        )
        return {"status": "refreshed", "business_id": business_id}

    except Exception as exc:
        logger.error("RAG refresh failed business_id=%s: %s", business_id, exc)
        raise self.retry(exc=exc, countdown=10)

    finally:
        if lock is not None:
            _release_lock(lock, lock_key)


@celery_app.task(bind=True, name="tasks.extract_metrics", max_retries=3, default_retry_delay=10)
def extract_metrics(self, business_id: str, content_type: str, doc_id: int, doc_content: str):
    from brand_metrics import BrandMetricsSQL
    analyzer = BrandMetricsSQL(business_id=business_id, content_type=content_type)
    try:
        inserted = analyzer.extract_and_save(doc_id=doc_id, doc_content=doc_content)
        if not inserted:
            return {"status": "skipped", "reason": "already_extracted"}

        analyzer.invalidate_cache()
        debounce_key = f"debounce_synthesis:{business_id}:{content_type}"
        
        if get_redis().set(debounce_key, "1", nx=True, ex=15):
            synthesize_metrics.apply_async(
                kwargs={"business_id": business_id, "content_type": content_type},
                countdown=15
            )
            logger.info("Queued debounced synthesis for %s", business_id)

        return {"status": "complete", "business_id": business_id, "doc_id": doc_id, "synthesis": "debounced"}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(bind=True, name="tasks.synthesize_metrics", max_retries=2, default_retry_delay=30)
def synthesize_metrics(self, business_id: str, content_type: str):
    lock_key = f"synthesize_lock:{business_id}:{content_type}"
    r = get_redis()
    lock = r.lock(lock_key, timeout=180)
    
    if not lock.acquire(blocking=False):
        logger.info("Synthesis already running for %s, skipping", business_id)
        return {"status": "skipped", "reason": "already_running"}
        
    try:
        from brand_metrics import BrandMetricsSQL
        analyzer = BrandMetricsSQL(business_id=business_id, content_type=content_type)
        logger.info("Running debounced synthesis for %s", business_id)
        analyzer.build_and_cache_context()
        return {"status": "complete", "business_id": business_id}
    except Exception as exc:
        logger.error("Debounced synthesis failed for %s: %s", business_id, exc)
        raise self.retry(exc=exc, countdown=30)
    finally:
        _release_lock(lock, lock_key)