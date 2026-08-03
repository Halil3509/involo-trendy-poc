from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import TransientError
from app.providers.trends import MetaHashtagTrendSource


def _source() -> MetaHashtagTrendSource:
    return MetaHashtagTrendSource(
        Settings(instagram_app_id="app-id", instagram_app_secret="secret")
    )


def _media_row(index: int) -> dict[str, Any]:
    return {
        "id": f"media-{index}",
        "permalink": f"https://www.instagram.com/p/{index}/",
        "timestamp": "2026-07-20T10:00:00+0000",
        "caption": "test caption",
        "media_type": "IMAGE",
        "media_url": "http://example.com/image.jpg",
        "like_count": 1,
        "comments_count": 0,
    }


@pytest.mark.asyncio
async def test_discover_paginates_and_reduces_page_size_on_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_get(
        path: str,
        access_token: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append((path, params))
        if "ig_hashtag_search" in path:
            return {"data": [{"id": "hashtag-1"}]}

        if params.get("limit", 0) >= 6:
            raise TransientError(
                "upstream returned 500 (500 for ... Please reduce the amount of data"
            )

        if params.get("after") is None:
            return {
                "data": [_media_row(i) for i in range(5)],
                "paging": {"cursors": {"after": "cursor-1"}},
            }

        return {
            "data": [_media_row(5)],
            "paging": {},
        }

    monkeypatch.setattr(source, "_get", fake_get)

    items = await source.discover(
        "recipe",
        access_token="token",
        instagram_business_account_id="user",
        limit=6,
    )

    assert len(items) == 6
    top_media_calls = [call for call in calls if "top_media" in call[0]]
    assert len(top_media_calls) == 3
    assert top_media_calls[0][1]["limit"] == 6
    assert top_media_calls[1][1]["limit"] == 5
    assert top_media_calls[2][1]["limit"] == 1
    assert all(call[1]["fields"] == source.MEDIA_FIELDS for call in top_media_calls)


@pytest.mark.asyncio
async def test_discover_returns_empty_on_persistent_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()

    async def fake_get(
        path: str,
        access_token: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if "ig_hashtag_search" in path:
            return {"data": [{"id": "hashtag-1"}]}
        raise TransientError(
            "upstream returned 500 (500 for ... Please reduce the amount of data"
        )

    monkeypatch.setattr(source, "_get", fake_get)

    items = await source.discover(
        "recipe",
        access_token="token",
        instagram_business_account_id="user",
        limit=10,
    )

    assert items == []


def _carousel_row() -> dict[str, Any]:
    return {
        "id": "carousel-1",
        "permalink": "https://www.instagram.com/p/carousel1/",
        "timestamp": "2026-07-20T10:00:00+0000",
        "caption": "carousel caption",
        "media_type": "CAROUSEL_ALBUM",
        "like_count": 5,
        "comments_count": 1,
        "children": {
            "data": [
                {"id": "child-1", "media_url": "http://example.com/first.jpg"},
                {"id": "child-2", "media_url": "http://example.com/second.jpg"},
            ]
        },
    }


@pytest.mark.asyncio
async def test_media_row_to_item_uses_first_child_url_for_carousels() -> None:
    source = _source()
    item = source._media_row_to_item(_carousel_row(), "recipe", "hashtag-1")
    assert item is not None
    assert item.media_url == "http://example.com/first.jpg"
    assert item.media_type == "CAROUSEL_ALBUM"
    assert item.provenance["media_edge"] == "top_media"


@pytest.mark.asyncio
async def test_media_row_to_item_returns_none_without_permalink_or_timestamp() -> None:
    source = _source()
    missing_permalink: dict[str, Any] = {
        "id": "x",
        "timestamp": "2026-07-20T10:00:00+0000",
    }
    missing_timestamp: dict[str, Any] = {
        "id": "x",
        "permalink": "https://www.instagram.com/p/x/",
    }
    assert source._media_row_to_item(missing_permalink, "recipe", "hashtag-1") is None
    assert source._media_row_to_item(missing_timestamp, "recipe", "hashtag-1") is None
