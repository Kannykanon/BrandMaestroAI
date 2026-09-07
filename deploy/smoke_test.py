# deploy/smoke_test.py
"""
Run this AFTER deploy_agent.py succeeds and AGENT_ENGINE_RESOURCE_NAME is
set, to confirm the deployed agent actually round-trips correctly against
your real Postgres/Redis — not just that deployment succeeded.

This checks exactly the two things flagged as unverified from the sandbox
that built this deploy path:
  1. remote_app.query(...) return shape matches what celery_task.py's
     _run_pipeline() expects (a GraphState-shaped dict).
  2. The Postgres/Redis-dependent nodes (rag, analyzer, memory via
     graph/deps.py) actually succeed when called from wherever Agent
     Platform Runtime executes them — this is where a VPC/network
     mismatch would surface as a hard failure, not a silent one.

Usage:
    export AGENT_ENGINE_RESOURCE_NAME=projects/.../reasoningEngines/...
    export PROJECT_ID=... LOCATION=us-central1
    export POSTGRES_URI=... REDIS_URL=...   # same DB the agent should reach
    python deploy/smoke_test.py [business_id]

If no business_id is given, one is generated and no brand documents will
exist for it — the Researcher node will still run (RAG query on an empty
index returns an empty/neutral result, it does not error), so this still
proves connectivity even without real brand content uploaded yet.
"""
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    resource_name = os.environ["AGENT_ENGINE_RESOURCE_NAME"]
    business_id = sys.argv[1] if len(sys.argv) > 1 else f"smoketest-{uuid.uuid4().hex[:8]}"
    generation_id = f"smoketest-gen-{uuid.uuid4().hex[:8]}"

    # 1. Seed a Generation row — deployer_node updates this by generation_id,
    #    so it must exist before the graph runs (mirrors what the FastAPI
    #    route does before dispatching a Celery task in production).
    from database import get_db_session, Generation

    with get_db_session() as session:
        session.add(Generation(
            generation_id=generation_id,
            business_id=business_id,
            content_type="blog",
            topic="Smoke test: season finale recap post",
            format_type="short",
            status="pending",
            created_at=datetime.utcnow(),
        ))
        session.commit()
    print(f"Seeded Generation row: {generation_id}")

    # 2. Call the deployed agent exactly the way celery_task._run_pipeline_remote does.
    import vertexai
    from vertexai import agent_engines

    vertexai.init(
        project=os.environ["PROJECT_ID"],
        location=os.getenv("LOCATION", "us-central1"),
    )
    remote_app = agent_engines.get(resource_name)

    initial_state = dict(
        business_id=business_id,
        content_type="blog",
        topic="Smoke test: season finale recap post",
        format_type="short",
        user_id=None,
        use_search=False,
        human_feedback=None,
        research="",
        content="",
        creative_angle="",
        iteration=0,
        approved=False,
        feedback="",
        flagged_passages="",
        score=0.0,
        generation_id=generation_id,
        status="pending",
    )

    print("Calling deployed agent...")
    result = remote_app.query(input=initial_state)

    # 3. Assertions — fail loudly and specifically rather than a generic crash.
    assert isinstance(result, dict), f"Expected dict back, got {type(result)}: {result!r}"

    expected_keys = {"content", "approved", "score", "iteration", "research"}
    missing = expected_keys - result.keys()
    assert not missing, (
        f"Result is missing expected GraphState keys: {missing}. "
        f"Got keys: {sorted(result.keys())}. "
        "This usually means remote_app.query() returns a wrapped/nested "
        "shape (e.g. {'output': {...}}) rather than the state dict "
        "directly — check the raw result printed above and adjust "
        "_run_pipeline_remote() in celery_task.py accordingly."
    )

    assert result["content"], (
        "content is empty — the graph ran but produced nothing. Check "
        "Agent Platform Runtime logs for the deployed agent; this is "
        "usually a DB/network connectivity failure inside the Writer "
        "node being swallowed somewhere, or a missing GOOGLE_API_KEY / "
        "PARALLEL_API_KEY in the deployed environment."
    )

    print("\n--- PASSED ---")
    print(f"approved: {result['approved']}, score: {result.get('score')}, "
          f"iteration: {result.get('iteration')}")
    print(f"content (first 300 chars): {result['content'][:300]}")

    # 4. Cleanup
    with get_db_session() as session:
        row = session.get(Generation, generation_id)
        if row:
            session.delete(row)
            session.commit()
    print(f"\nCleaned up Generation row: {generation_id}")


if __name__ == "__main__":
    main()
