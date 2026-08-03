from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.resources import utcnow
from app.providers.scraper import EmitFn, ScraperAdapter, noop_emit
from app.schemas.trends import ScrapedItem


class ScraperService:
    def __init__(self, db: AsyncDatabase[dict[str, Any]], adapter: ScraperAdapter) -> None:
        self.db = db
        self.adapter = adapter

    async def _update_progress(
        self,
        job_id: str,
        progress: dict[str, Any],
        step: str | None,
        keyword: str | None,
        count: int | None,
    ) -> None:
        """Persist live scraper progress to the job document."""
        if keyword:
            progress["current_keyword"] = keyword
            entry = next(
                (k for k in progress["keywords"] if k.get("name") == keyword),
                None,
            )
            if entry is None:
                entry = {"name": keyword, "discovered": 0, "status": "running"}
                progress["keywords"].append(entry)
            if step == "collected":
                entry["status"] = "completed"
                if count is not None:
                    entry["discovered"] = count
                progress["total_discovered"] = sum(
                    k.get("discovered", 0) for k in progress["keywords"]
                )
            elif step == "keyword":
                entry["status"] = "opening"
            elif step == "collect":
                entry["status"] = "collecting"
        elif step:
            progress["current_step"] = step

        await self.db.job_runs.update_one(
            {"task_id": job_id},
            {"$set": {"progress": progress}},
        )

    async def run(
        self,
        keywords: list[str],
        limit: int,
        on_event: EmitFn = noop_emit,
        *,
        job_id: str | None = None,
    ) -> dict[str, int]:
        await on_event(
            f"Scrape started for keywords: {', '.join(keywords)}.",
            level="info",
            step="start",
        )
        adapter_metrics: dict[str, int] = {}
        progress: dict[str, Any] = {
            "current_keyword": None,
            "current_step": "start",
            "keywords": [],
            "total_discovered": 0,
            "total_target": len(keywords) * limit,
        }

        async def emit(
            message: str,
            *,
            level: str = "info",
            step: str | None = None,
            **data: Any,
        ) -> None:
            for key in ("failed_keywords", "keywords_count"):
                if key in data and isinstance(data[key], int):
                    adapter_metrics[key] = data[key]
            if job_id and step in ("keyword", "collect", "collected"):
                await self._update_progress(
                    job_id,
                    progress,
                    step,
                    data.get("keyword"),
                    data.get("count"),
                )
            await on_event(message, level=level, step=step, **data)

        existing_cache: dict[str, bool] = {}

        async def is_existing(canonical_url: str) -> bool:
            if canonical_url in existing_cache:
                return existing_cache[canonical_url]
            doc = await self.db.trend_content.find_one(
                {"canonical_url": canonical_url}, {"_id": 1, "processing_status": 1}
            )
            # Posts whose media URL expired are re-scraped to obtain a fresh URL.
            exists = doc is not None and doc.get("processing_status") != "media_expired"
            existing_cache[canonical_url] = exists
            return exists

        items = await self.adapter.scrape(
            keywords, limit, emit, is_existing=is_existing
        )
        counters = {
            "discovered": len(items),
            "inserted": 0,
            "updated": 0,
            "failed_keywords": adapter_metrics.get("failed_keywords", 0),
            "keywords_count": adapter_metrics.get("keywords_count", len(keywords)),
        }
        for item in items:
            result = await self.upsert(item, job_id=job_id)
            counters["inserted" if result else "updated"] += 1
        progress["current_step"] = "done"
        progress["total_discovered"] = counters["discovered"]
        if job_id:
            await self._update_progress(
                job_id, progress, "done", progress.get("current_keyword"), None
            )
        await on_event(
            (
                f"Scrape finished: {counters['discovered']} discovered, "
                f"{counters['inserted']} new, {counters['updated']} updated, "
                f"{counters['failed_keywords']} keyword(s) failed."
            ),
            level=(
                "success"
                if counters["discovered"] or not counters["keywords_count"]
                else "error"
            ),
            step="done",
            counters=counters,
            failed_keywords=counters["failed_keywords"],
        )
        return counters

    async def upsert(self, item: ScrapedItem, *, job_id: str | None = None) -> bool:
        now = utcnow()
        values = item.model_dump(exclude={"discovered_keyword", "caption", "metadata"})
        if item.caption:
            values["caption_text"] = item.caption
        if item.metadata.get("video_url"):
            values["video_url"] = item.metadata["video_url"]
        else:
            values["video_url"] = None
        values["metadata"] = item.metadata
        action = None
        result = await self.db.trend_content.update_one(
            {"canonical_url": item.canonical_url},
            {
                "$set": {**values, "last_seen_at": now},
                "$setOnInsert": {
                    "first_seen_at": now,
                    "created_at": now,
                    "processing_status": "discovered",
                },
                "$addToSet": {"discovered_keywords": item.discovered_keyword},
            },
            upsert=True,
        )
        inserted = result.upserted_id is not None
        action = "inserted" if inserted else "updated"
        if not inserted and values.get("video_url"):
            # A re-scraped post with a fresh media URL re-enters the pipeline.
            await self.db.trend_content.update_one(
                {"canonical_url": item.canonical_url, "processing_status": "media_expired"},
                {
                    "$set": {"processing_status": "discovered"},
                    "$unset": {"enrichment_error": "", "processing_error": ""},
                },
            )
        if job_id is not None:
            await self.db.trend_content.update_one(
                {"canonical_url": item.canonical_url},
                {
                    "$set": {
                        "last_scrape_job_id": job_id,
                        "last_upsert_action": action,
                    }
                },
            )
        return inserted
