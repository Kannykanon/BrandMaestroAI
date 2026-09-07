import os, asyncio
from dotenv import load_dotenv
load_dotenv()
from model import LLMSingleton
from brand_metrics import BrandMetricsSQL
from database import get_db_session

async def main():
    llm = LLMSingleton.get("synthesis")
    metrics = BrandMetricsSQL("be6686dc-0442-4fdb-be79-6ec23204d840", "blog")
    
    with get_db_session() as session:
        from database import BrandMetric
        rows = session.query(BrandMetric).filter_by(
            business_id=metrics.business_id,
            content_type=metrics.content_type
        ).order_by(BrandMetric.created_at.asc()).all()
        
    recent = rows[-10:]
    profiles_block = metrics._format_profiles_for_synthesis(recent)
    
    from prompts.metrics import METRICS_SYNTHESIS, METRICS_SYNTHESIS_TEMPLATE
    synthesis_prompt = METRICS_SYNTHESIS.format(
        business_id=metrics.business_id,
        content_type=metrics.content_type,
        total_documents=len(rows),
        profiles=profiles_block,
    )
    synthesis_prompt += "\n\n" + METRICS_SYNTHESIS_TEMPLATE
    
    print("Calling LLM...")
    result = llm.invoke(synthesis_prompt)
    print("Finish reason:", getattr(result, "response_metadata", "no response_metadata"))
    print("Output length:", len(result.content))


if __name__ == "__main__":
    asyncio.run(main())
