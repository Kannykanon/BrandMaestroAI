# chaos/experiment_1_worker_crash.py
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["POSTGRES_URI"] = "postgresql://brandguard:brandguard@localhost:5433/brandguard"

import subprocess
import time
import json
import requests
import logging
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_steady_state() -> bool:
    try:
        response = requests.get("http://localhost:8000/health", timeout=15)
        return response.status_code == 200
    except Exception:
        return False


def stream_generation(topic: str, result: dict) -> None:
    start = time.monotonic()
    try:
        response = requests.post(
            "http://localhost:8000/conversation/generate/stream",
            json={
                "business_id": "test_business",
                "content_type": "blog",
                "topic": topic,
                "format_type": "blog",
                "use_search": False
            },
            stream=True,
            timeout=300
        )

        if response.status_code != 200:
            logger.error("API error %d: %s", response.status_code, response.text[:100])
            result["status"] = "failed"
            result["elapsed"] = -1
            return

        completed = False

        try:
            for chunk in response.iter_content(chunk_size=None):
                if chunk:
                    try:
                        data = json.loads(chunk.decode("utf-8"))

                        # Grab generation_id from first chunk (API-level)
                        if data.get("generation_id") and not result.get("generation_id"):
                            result["generation_id"] = data["generation_id"]

                        # Also check node outputs (worker-level)
                        for node_output in data.values():
                            if isinstance(node_output, dict):
                                if node_output.get("generation_id") and not result.get("generation_id"):
                                    result["generation_id"] = node_output["generation_id"]
                                if node_output.get("status") == "completed":
                                    completed = True
                    except Exception:
                        pass
        except requests.exceptions.ChunkedEncodingError:
            logger.warning("Stream ended prematurely — switching to polling")

        result["elapsed"] = time.monotonic() - start
        result["status"] = "complete" if completed else "stream_ended"

    except Exception as exc:
        logger.error("Generation stream failed: %s", exc)
        result["status"] = "failed"
        result["elapsed"] = -1

def inject_failure():
    logger.info("CHAOS: stopping generation worker...")
    subprocess.run(
        ["docker", "compose", "stop", "worker_generation"],
        capture_output=True
    )
    logger.info("CHAOS: Worker stopped")


def rollback():
    logger.info("ROLLBACK: Restarting generation worker...")
    subprocess.run(
        ["docker", "compose", "start", "worker_generation"],
        capture_output=True
    )
    logger.info("ROLLBACK: Worker restarted")


def observe_recovery(result: dict, timeout: int = 180) -> list:
    """
    Poll DB directly for generation status after worker restart.
    DB is source of truth — not the stream.
    """
    from database import get_db_session, Generation

    observations = []
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        elapsed = round(time.monotonic() - start, 2)
        generation_id = result.get("generation_id")
        stream_status = result.get("status", "pending")

        # Check DB directly
        db_status = "not_found"
        try:
            if generation_id:
                with get_db_session() as session:
                    record = session.get(Generation, generation_id)
                    db_status = record.status if record else "not_found"
        except Exception as exc:
            db_status = f"db_error: {exc}"

        observations.append({
            "elapsed": elapsed,
            "stream_status": stream_status,
            "db_status": db_status
        })

        logger.info(
            "elapsed=%.2fs stream=%s db=%s generation_id=%s",
            elapsed, stream_status, db_status, generation_id or "none"
        )

        if db_status == "completed":
            result["status"] = "complete"
            logger.info("[OK] Generation completed in DB — task survived crash")
            break

        time.sleep(15)

    return observations


def run():
    print("\n" + "="*60)
    print("EXPERIMENT 1 — Task Durability: Worker Crash")
    print("="*60)

    if not check_steady_state():
        print("ABORTED: System not healthy. Fix before running chaos.")
        return

    print("[OK] Steady state verified")

    # Start generation in background thread
    result = {
        "generation_id": None,
        "elapsed": -1,
        "status": "pending"
    }

    thread = threading.Thread(
        target=stream_generation,
        args=("product launch", result)
    )
    thread.start()
    print("[OK] Generation started (streaming)")

    # Wait for worker to pick up task
    print("  Waiting 5s for worker to pick up task...")
    time.sleep(15)
    print("[OK] Worker should be processing task now")

    # Kill worker mid generation
    inject_failure()
    print("CHAOS: Worker killed mid-generation")

    # Observe immediate impact
    print("\nObserving immediate impact (15s)...")
    time.sleep(15)
    print(f"  Stream status after kill: {result.get('status', 'pending')}")
    print(f"  Generation ID captured:   {result.get('generation_id') or 'none yet'}")

    # Rollback
    rollback()
    print("ROLLBACK: Worker restarted")

    # Observe recovery via DB polling
    print("\nObserving recovery via DB polling...")
    observations = observe_recovery(result, timeout=180)

    # Wait for stream thread to finish
    thread.join(timeout=10)

    # Report
    print("\n" + "-"*60)
    print("RESULTS:")
    print("-"*60)

    generation_id = result.get("generation_id")
    final_db_status = observations[-1]["db_status"] if observations else "unknown"
    final_stream_status = result.get("status", "unknown")
    total_time = result.get("elapsed", -1)

    print(f"Generation ID:    {generation_id or 'None'}")
    print(f"Stream status:    {final_stream_status}")
    print(f"DB status:        {final_db_status}")
    print(f"Stream time:      {total_time:.2f}s" if total_time > 0 else "Stream time:      N/A")

    print("\nTimeline:")
    for obs in observations:
        print(
            f"  {obs['elapsed']}s → "
            f"stream={obs['stream_status']} "
            f"db={obs['db_status']}"
        )

    print("\nHYPOTHESIS:")
    if final_db_status == "completed":
        print("PASSED: Task survived worker crash and completed")
        print("  Celery task durability is working correctly")
        print("  acks_late + reject_on_worker_lost working as expected")
        print(f"  generation_id: {generation_id}")
    elif generation_id and final_db_status == "not_found":
        print("PARTIAL: Task requeued but generation not saved to DB")
        print("  ACTION: Check deployer node DB write")
        print("  ACTION: Check Generation model status update")
    elif not generation_id:
        print("FAILED: Stream ended before generation_id was captured")
        print("  Worker killed too early — increase wait time before kill")
        print("  Or check /generate polling endpoint as fallback")
    else:
        print("FAILED: Task did not survive worker crash")
        print("  ACTION: Verify acks_late=True in celery_task.py")
        print("  ACTION: Verify task_reject_on_worker_lost=True")
        print("  ACTION: Verify durable=True on all queues")

    print("="*60)


if __name__ == "__main__":
    run()