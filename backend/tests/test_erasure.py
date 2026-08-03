from typing import Any

import pytest
from bson import ObjectId
from fakes import FakeDatabase

from app.core.config import Settings
from app.services.erasure import InstagramErasureError, InstagramErasureService


class RecordingMedia:
    def __init__(self, fail: bool = False) -> None:
        self.keys: list[str] = []
        self.fail = fail

    async def delete_assets(self, assets: list[dict[str, str]]) -> None:
        self.keys = sorted(asset["key"] for asset in assets)
        if self.fail:
            raise RuntimeError("S3 unavailable")


class RecordingQdrant:
    def __init__(self) -> None:
        self.deletes: list[dict[str, Any]] = []

    async def delete(self, **kwargs: Any) -> None:
        self.deletes.append(kwargs)


@pytest.mark.asyncio
async def test_instagram_erasure_deletes_assets_vectors_and_business_data() -> None:
    db = FakeDatabase()
    user_id = ObjectId()
    db.users.docs.append({"_id": user_id, "email": "preserved@example.test"})
    db.user_content.docs.append(
        {
            "user_id": user_id,
            "media_asset": {"bucket": "media", "key": "media/original.mp4"},
            "keyframes": [{"bucket": "media", "key": "frames/0.jpg"}],
            "video_segments": [
                {
                    "bucket": "media",
                    "key": "segments/0.mp4",
                    "embedding_asset": {
                        "bucket": "embedding-media",
                        "key": "embedding/0.mp4",
                    },
                }
            ],
        }
    )
    db.instagram_connections.docs.append({"user_id": user_id})
    db.provider_runs.docs.append({"user_id": str(user_id)})
    media = RecordingMedia()
    qdrant = RecordingQdrant()

    result = await InstagramErasureService(
        db, qdrant, media, Settings()
    ).erase(user_id)

    assert media.keys == [
        "embedding/0.mp4",
        "frames/0.jpg",
        "media/original.mp4",
        "segments/0.mp4",
    ]
    assert [item["collection_name"] for item in qdrant.deletes] == [
        "user_profiles_v2",
        "user_content_v2",
        "content_segments_v2",
    ]
    assert result["s3_objects"] == 4
    assert db.user_content.docs == []
    assert db.provider_runs.docs == []
    assert db.users.docs == [{"_id": user_id, "email": "preserved@example.test"}]


@pytest.mark.asyncio
async def test_instagram_erasure_failure_preserves_mongo_for_retry() -> None:
    db = FakeDatabase()
    user_id = ObjectId()
    db.user_content.docs.append(
        {
            "user_id": user_id,
            "media_asset": {"bucket": "media", "key": "media/original.mp4"},
        }
    )

    with pytest.raises(InstagramErasureError):
        await InstagramErasureService(
            db, RecordingQdrant(), RecordingMedia(fail=True), Settings()
        ).erase(user_id)

    assert len(db.user_content.docs) == 1
