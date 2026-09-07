locust -f chaos/experiment_4_latency.py \
       --host http://localhost:8000 \
       --users 50 \
       --spawn-rate 5 \
       --run-time 5m \
       --headless \
       --html chaos/reports/latency_report.html


# chaos/run_all.py
import subprocess
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

experiments = [
    ("Experiment 1 — Task Durability",    "chaos/experiment_1_worker_crash.py"),
    ("Experiment 2 — Cache Consistency",  "chaos/experiment_2_redis_restart.py"),
    ("Experiment 3 — Data Integrity",     "chaos/experiment_3_data_integrity.py"),
]

def run():
    print("\n" + "="*60)
    print("BRANDGUARD AI — CHAOS ENGINEERING SUITE")
    print("="*60)

    results = []

    for name, path in experiments:
        print(f"\nRunning: {name}")
        print("-"*40)

        result = subprocess.run(
            [sys.executable, path],
            capture_output=False
        )

        results.append({
            "name": name,
            "passed": result.returncode == 0
        })

        # Wait between experiments for system to stabilize
        print("\nWaiting 30s for system to stabilize...")
        import time
        time.sleep(30)

    # Final report
    print("\n" + "="*60)
    print("CHAOS SUITE RESULTS")
    print("="*60)

    for r in results:
        status = "PASSED" if r["passed"] else "FAILED"
        print(f"{status}: {r['name']}")

    passed = sum(1 for r in results if r["passed"])
    print(f"\n{passed}/{len(results)} experiments passed")
    print("="*60)

if __name__ == "__main__":
    run()



1. experiment_3 first — data integrity, no failure injection
   Just verifies your happy path works correctly

2. experiment_2 — Redis restart, low risk
   System should recover automatically

3. experiment_1 — Worker crash, medium risk
   Tests your core reliability claim

4. experiment_4 — Load test last
   Only after single request reliability is confirmed


# Make sure stack is running
docker compose up -d

# Verify everything is healthy
curl http://localhost:8000/health

# Check all containers are up
docker compose ps