# chaos/experiment_4_latency_new.py
from locust import HttpUser, task, between
import time
import logging

logger = logging.getLogger(__name__)

class BrandGuardUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.generation_ids = []

    @task(5)
    def generate_and_poll(self):
        # Queue generation
        with self.client.post(
            "/generate",
            json={
                "business_id": "test_business",
                "content_type": "blog",
                "topic": "product launch",
                "format_type": "blog",
                "use_search": False
            },
            catch_response=True
        ) as response:
            if response.status_code == 503:
                response.success()  # queue full, not a failure — expected under load
                return
            if response.status_code != 200:
                response.failure(f"Queue failed: {response.status_code}")
                return
            try:
                generation_id = response.json()["generation_id"]
            except Exception:
                response.failure("Invalid response from /generate")
                return

        # Poll for result — use wall clock instead of fixed iterations
        timeout = 120          # 2 minutes max wait
        poll_interval = 5      # check every 5s
        deadline = time.monotonic() + timeout
        start = time.monotonic()

        while time.monotonic() < deadline:
            time.sleep(poll_interval)

            with self.client.get(
                f"/result/{generation_id}",
                name="/result/[generation_id]",
                catch_response=True
            ) as result:
                if not result.text:
                    result.success()
                    continue

                try:
                    data = result.json()
                except Exception:
                    result.success()
                    continue

                status = data.get("status")

                if status == "completed":
                    elapsed = time.monotonic() - start
                    if elapsed > 90:   # realistic threshold — not 60s
                        result.failure(f"Too slow: {elapsed:.1f}s")
                    else:
                        result.success()
                    self.generation_ids.append(generation_id)
                    return

                elif status in ("failed", "timeout"):
                    result.failure(f"Generation {status}: {generation_id}")
                    return

                else:
                    result.success()  # pending/processing — keep waiting

        logger.warning("Generation timed out after 2 minutes: %s", generation_id)

    @task(2)
    def submit_feedback(self):
        if not self.generation_ids:
            return

        generation_id = self.generation_ids.pop(0)
        self.client.post("/feedback", json={
            "generation_id": generation_id,
            "business_id": "test_business",
            "content_type": "blog",
            "human_approved": True,
            "human_score": 8.5,
            "human_feedback": "Great brand voice consistency"
        })

    @task(1)
    def health_check(self):
        self.client.get("/health")