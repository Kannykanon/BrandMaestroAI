# chaos/experiment_3_data_integrity.py
import sys
import os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["POSTGRES_URI"] = "postgresql://brandguard:brandguard@localhost:5433/brandguard"

import requests
import time
import logging
from database import get_db_session, GenerationFeedback, Generation
import json

logger = logging.getLogger(__name__)


def run_generation(topic: str) -> str:
    response = requests.post("http://localhost:8000/conversation/generate/stream", json={
        "business_id": "test_business",
        "content_type": "blog",
        "topic": topic,
        "format_type": "blog",
        "use_search": False
    }, stream=True, timeout=300)

    print(f"Status code: {response.status_code}")

    if response.status_code != 200:
        print(f"API error: {response.text[:100]}")
        return None

    generation_id = None
    try:
        for chunk in response.iter_content(chunk_size=None):
            if chunk:
                try:
                    data = json.loads(chunk.decode("utf-8"))
                    if data.get("generation_id"):
                        generation_id = data["generation_id"]
                except Exception:
                    pass
    except requests.exceptions.ChunkedEncodingError:
        logger.warning("Stream ended prematurely for topic=%r", topic)

    print(f"  generation_id: {generation_id}")
    return generation_id


def verify_in_db(generation_id: str) -> bool:
    if not generation_id or len(generation_id) > 100:
        return False

    with get_db_session() as session:
        record = session.get(Generation, generation_id)
        if not record:
            print(f"  [FAIL] Record not found in DB at all")
            return False

        print(f"  content: {str(record.content)[:50] if record.content else None}")
        print(f"  score: {record.score}")
        print(f"  content_type: {record.content_type}")
        print(f"  topic: {record.topic}")
        print(f"  status: {record.status}")

        checks = {
            "content": bool(record.content),
            "score": record.score is not None,
            "content_type": bool(record.content_type),
            "topic": bool(record.topic),
            "status": record.status == "completed"
        }

        failed = [k for k, v in checks.items() if not v]
        if failed:
            print(f"  [FAIL] Failed checks: {failed}")
            return False

        return True


def run():
    print("\n" + "="*60)
    print("EXPERIMENT 3 — Data Integrity")
    print("="*60)

    topics = [
        "product launch announcement",
        "sustainability initiative",
        "customer success story",
        "new feature release",
        "company milestone"
    ]

    results = []

    for topic in topics:
        print(f"\nGenerating: '{topic}'...")
        generation_id = run_generation(topic)

        if not generation_id:
            results.append({
                "topic": topic,
                "generation_id": None,
                "saved_correctly": False,
                "error": "Generation failed or timed out"
            })
            continue

        # Give worker time to finish DB write
        time.sleep(15)

        saved = verify_in_db(generation_id)
        results.append({
            "topic": topic,
            "generation_id": generation_id,
            "saved_correctly": saved,
            "error": None if saved else "Missing fields in DB"
        })

        print(f"  generation_id: {generation_id}")
        print(f"  saved correctly: {'[OK]' if saved else '[FAIL]'}")

    # Report
    print("\n" + "-"*60)
    print("RESULTS:")
    print("-"*60)

    passed = sum(1 for r in results if r["saved_correctly"])
    failed = len(results) - passed

    print(f"Total generations: {len(results)}")
    print(f"Saved correctly:   {passed}")
    print(f"Failed:            {failed}")

    if failed > 0:
        print("\nFAILED GENERATIONS:")
        for r in results:
            if not r["saved_correctly"]:
                print(f"  Topic: {r['topic']}")
                print(f"  Error: {r['error']}")

    print("\nHYPOTHESIS:")
    if failed == 0:
        print("PASSED: All generations saved correctly")
    else:
        print("FAILED: Data integrity issues found")
        print("  ACTION: Check deployer node error handling")
        print("  ACTION: Check Generation model required fields")

    print("="*60)


if __name__ == "__main__":
    run()