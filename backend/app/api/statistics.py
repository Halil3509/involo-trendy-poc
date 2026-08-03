from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.schemas.trends import PipelineStats


async def compute_pipeline_stats(
    db: AsyncDatabase[dict[str, Any]],
) -> PipelineStats:
    trend = db.trend_content
    return PipelineStats(
        discovered=await trend.count_documents({"processing_status": "discovered"}),
        enriched=await trend.count_documents({"processing_status": "enriched"}),
        stored=await trend.count_documents({"processing_status": "stored"}),
        embedded=await trend.count_documents({"processing_status": "embedded"}),
        needs_intervention=await trend.count_documents(
            {"processing_status": "needs_intervention"}
        ),
        failed=await trend.count_documents({"processing_status": "failed"}),
    )
