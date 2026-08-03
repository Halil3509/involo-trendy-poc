from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from provider_doubles import FixtureScraper

from app.schemas.trends import ScrapedItem
from app.services.scraper import ScraperService


class FakeTrendContent:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    async def find_one(
        self, query: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        document = self.documents.get(query.get("canonical_url", ""))
        if document is None:
            return None
        result: dict[str, Any] = {"_id": query["canonical_url"]}
        for key in projection or {}:
            if key in document:
                result[key] = document[key]
        return result

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> SimpleNamespace:
        url = query["canonical_url"]
        if "processing_status" in query:
            document = self.documents.get(url)
            if document is None or document.get("processing_status") != query["processing_status"]:
                return SimpleNamespace(upserted_id=None)
            document.update(update.get("$set", {}))
            for key in update.get("$unset", {}):
                document.pop(key, None)
            return SimpleNamespace(upserted_id=None)
        assert upsert
        inserted = url not in self.documents
        document = self.documents.setdefault(url, {})
        if inserted:
            document.update(update["$setOnInsert"])
        document.update(update["$set"])
        document.setdefault("discovered_keywords", [])
        keyword = update["$addToSet"]["discovered_keywords"]
        if keyword not in document["discovered_keywords"]:
            document["discovered_keywords"].append(keyword)
        return SimpleNamespace(upserted_id="new" if inserted else None)


@pytest.mark.asyncio
async def test_service_upsert_preserves_first_seen_and_keywords() -> None:
    fixture = Path(__file__).parent / "fixtures" / "instagram.json"
    collection = FakeTrendContent()
    db = SimpleNamespace(trend_content=collection)
    service = ScraperService(db, FixtureScraper(fixture))  # type: ignore[arg-type]

    first = await service.run(["travel"], 10)
    first_seen = collection.documents["https://www.instagram.com/reel/Fixture_A1/"]["first_seen_at"]
    second = await service.run(["travel"], 10)
    document = collection.documents["https://www.instagram.com/reel/Fixture_A1/"]

    assert first["inserted"] == 1
    assert second["updated"] == 1
    assert document["first_seen_at"] == first_seen
    assert document["discovered_keywords"] == ["travel"]


@pytest.mark.asyncio
async def test_media_expired_post_reenters_pipeline_with_fresh_url() -> None:
    fixture = Path(__file__).parent / "fixtures" / "instagram.json"
    collection = FakeTrendContent()
    db = SimpleNamespace(trend_content=collection)
    service = ScraperService(db, FixtureScraper(fixture))  # type: ignore[arg-type]
    await service.run(["travel"], 10)
    url = "https://www.instagram.com/reel/Fixture_A1/"
    document = collection.documents[url]
    document["processing_status"] = "media_expired"
    document["enrichment_error"] = "media download failed"

    item = ScrapedItem(
        canonical_url=url,
        shortcode="Fixture_A1",
        caption="A deterministic travel fixture",
        discovered_keyword="travel",
        metadata={"video_url": "https://cdn.example.test/fresh.mp4"},
    )
    await service.upsert(item)

    assert document["processing_status"] == "discovered"
    assert document["video_url"] == "https://cdn.example.test/fresh.mp4"
    assert "enrichment_error" not in document
