import os
from dotenv import load_dotenv
load_dotenv()
from brand_metrics import BrandMetricsSQL
from model import LLMSingleton

# Test the LLM directly first
llm = LLMSingleton.get("synthesis")
print("Testing LLM directly...")
try:
    result = llm.invoke("Say hello in one sentence.")
    print(f"Direct test result: '{result.content}'")
    print(f"Response metadata: {result.response_metadata}")
except Exception as e:
    print(f"Direct LLM call failed: {e}")
    import sys; sys.exit(1)

print("\n---")
print("Now running full synthesis...")
metrics = BrandMetricsSQL("be6686dc-0442-4fdb-be79-6ec23204d840", "blog")

# Get the profiles to check what's there
from database import get_db_session, BrandMetrics
with get_db_session() as session:
    rows = session.query(BrandMetrics).filter_by(
        business_id="be6686dc-0442-4fdb-be79-6ec23204d840",
        content_type="blog"
    ).all()
    print(f"Found {len(rows)} metric rows in DB")

result = metrics.build_and_cache_context()
print(f"\nSynthesis output length: {len(result)} characters")
if result:
    print(f"First 300 chars:\n{result[:300]}")
    print(f"\nLast 300 chars:\n{result[-300:]}")
