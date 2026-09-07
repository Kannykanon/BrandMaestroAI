# chaos/experiment_2_redis_restart.py
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["POSTGRES_URI"] = "postgresql://brandguard:brandguard@localhost:5433/brandguard"

import subprocess
import time
import json
import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_steady_state() -> bool:
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def run_generation(topic: str) -> tuple[str | None, float]:
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
            return None, -1

        generation_id = None
        try:
            for chunk in response.iter_content(chunk_size=None):
                if chunk:
                    try:
                        data = json.loads(chunk.decode("utf-8"))

                        # First chunk — top level generation_id
                        if data.get("generation_id") and not generation_id:
                            generation_id = data["generation_id"]

                        # Node outputs
                        for node_output in data.values():
                            if isinstance(node_output, dict) and node_output.get("generation_id"):
                                if not generation_id:
                                    generation_id = node_output["generation_id"]
                    except Exception:
                        pass
        except requests.exceptions.ChunkedEncodingError:
            logger.warning("Stream ended prematurely — generation may still be processing")

        elapsed = time.monotonic() - start
        return generation_id, elapsed

    except Exception as exc:
        logger.error("Generation request failed: %s", exc)
        return None, -1


def inject_failure():
    """Restart Redis mid operation."""
    logger.info("CHAOS: Restarting Redis...")
    subprocess.run(["docker", "compose", "restart", "redis"], capture_output=True)
    logger.info("CHAOS: Redis restarted")


def run():
    print("\n" + "="*60)
    print("EXPERIMENT 2 — Cache Consistency: Redis Restart")
    print("="*60)

    if not check_steady_state():
        print("ABORTED: System not healthy. Start your stack first.")
        return

    print("[OK] Steady state verified")

    # ── Baseline ──────────────────────────────────────────────
    print("\nBaseline: Measuring normal generation time...")
    baseline_id, baseline_time = run_generation("baseline test")

    if baseline_id is None or baseline_time == -1:
        print("[FAIL] Baseline generation failed — cannot continue experiment")
        return

    print(f"[OK] Baseline generation_id: {baseline_id}")
    print(f"[OK] Baseline generation time: {baseline_time:.2f}s")

    # ── Queue second generation ────────────────────────────────
    print("\nQueuing generation for chaos test...")

    # Start the request in a thread so we can restart Redis mid-stream
    import threading

    chaos_result = {"generation_id": None, "elapsed": -1}

    def chaos_generation():
        gid, elapsed = run_generation("chaos test after redis restart")
        chaos_result["generation_id"] = gid
        chaos_result["elapsed"] = elapsed

    thread = threading.Thread(target=chaos_generation)
    thread.start()

    # Give the generation a moment to start
    time.sleep(3)

    # ── Inject failure ─────────────────────────────────────────
    inject_failure()
    print("CHAOS: Redis restarted mid-generation")

    # ── Wait for result ────────────────────────────────────────
    print("\nWaiting for generation to complete...")
    thread.join(timeout=300)

    recovery_time = chaos_result["elapsed"]
    chaos_id = chaos_result["generation_id"]

    # ── Report ─────────────────────────────────────────────────
    print("\n" + "-"*60)
    print("RESULTS:")
    print("-"*60)
    print(f"Baseline generation time:       {baseline_time:.2f}s")

    if recovery_time == -1 or chaos_id is None:
        print(f"Post-Redis-restart time:        FAILED")
        print("\nFAILED: Generation did not complete after Redis restart")
        print("  ACTION: Check Redis AOF persistence config")
        print("          appendonly yes")
        print("          appendfsync everysec")
        print("  ACTION: Check Celery broker reconnection settings")
        print("  ACTION: Verify RabbitMQ stays healthy during Redis restart")
    elif recovery_time > baseline_time * 3:
        print(f"Post-Redis-restart time:        {recovery_time:.2f}s")
        print(f"\nWARNING: Significant latency increase")
        print(f"  Baseline: {baseline_time:.2f}s → After restart: {recovery_time:.2f}s")
        print("  ACTION: Check metrics cache rebuild time after Redis restart")
        print("  ACTION: Consider Redis Sentinel for automatic failover")
    else:
        print(f"Post-Redis-restart time:        {recovery_time:.2f}s")
        print(f"\nPASSED: System recovered gracefully")
        print(f"  Latency increase: {recovery_time - baseline_time:.2f}s — acceptable")

    print("="*60)


if __name__ == "__main__":
    run()