import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.providers.media import S3MediaProvider, StoredMedia


class DownloadClient:
    def download_file(self, bucket: str, key: str, filename: str) -> None:
        Path(filename).write_bytes(b"video")


def test_keyframes_skip_offsets_beyond_short_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = S3MediaProvider(Settings(media_s3_bucket="media"))
    monkeypatch.setattr(provider, "_client", lambda: DownloadClient())
    monkeypatch.setattr(provider, "_upload", lambda *args: None)

    def run(command: list[str], **kwargs: Any) -> Any:
        offset = command[command.index("-ss") + 1]
        if offset == "5.0":
            raise subprocess.CalledProcessError(1, command)
        Path(command[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    stored = StoredMedia("media", "source.mp4", "s3://media/source.mp4", "video/mp4", "x", 5)

    frames = provider._extract_keyframes_sync(stored, "short-video", [0.0, 5.0])

    assert [frame.offset_seconds for frame in frames] == [0.0]


def test_direct_video_analysis_is_allowed_when_no_keyframe_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = S3MediaProvider(Settings(media_s3_bucket="media"))
    monkeypatch.setattr(provider, "_client", lambda: DownloadClient())

    def fail(command: list[str], **kwargs: Any) -> Any:
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", fail)
    stored = StoredMedia("media", "source.mp4", "s3://media/source.mp4", "video/mp4", "x", 5)

    assert provider._extract_keyframes_sync(stored, "tiny-video", [1.0, 3.0]) == []


def test_embedding_media_is_copied_to_region_compatible_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        media_s3_bucket="media-eu",
        media_s3_region="eu-central-1",
        embedding_media_s3_bucket="media-us",
        embedding_media_s3_region="us-east-1",
    )
    provider = S3MediaProvider(settings)
    uploaded: dict[str, str] = {}

    class SourceClient:
        def download_file(self, bucket: str, key: str, filename: str) -> None:
            Path(filename).write_bytes(b"segment")

    monkeypatch.setattr(provider, "_client", lambda: SourceClient())
    monkeypatch.setattr(provider, "_embedding_client", lambda: object())

    def upload(
        client: Any,
        bucket: str,
        filename: str,
        key: str,
        content_type: str,
        use_server_side_encryption: bool = True,
    ) -> None:
        uploaded.update(bucket=bucket, key=key, content_type=content_type)

    monkeypatch.setattr(provider, "_upload_to", upload)
    source = StoredMedia(
        "media-eu",
        "segments/a.mp4",
        "s3://media-eu/segments/a.mp4",
        "video/mp4",
        "abc",
        7,
        "eu-central-1",
    )

    mirrored = provider._prepare_embedding_media_sync(source)

    assert mirrored.bucket == "media-us"
    assert mirrored.region == "us-east-1"
    assert uploaded == {
        "bucket": "media-us",
        "key": "content-intelligence/embedding/segments/a.mp4",
        "content_type": "video/mp4",
    }


def test_s3_client_ignores_empty_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = S3MediaProvider(
        Settings(media_s3_bucket="media", transcribe_s3_endpoint_url="")
    )
    captured: dict[str, Any] = {}

    def fake_boto3_client(service: str, **kwargs: Any) -> object:
        captured["options"] = kwargs
        return object()

    monkeypatch.setattr("boto3.client", fake_boto3_client)
    provider._s3_client("eu-central-1", "")

    assert "endpoint_url" not in captured["options"]


def test_ingest_sends_browser_headers_for_instagram_cdn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = S3MediaProvider(Settings(media_s3_bucket="media"))
    monkeypatch.setattr(provider, "_upload", lambda *args, **kwargs: None)

    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> Any:
            yield b"0123456789"

    class FakeStream:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

        def __enter__(self) -> FakeResponse:
            return FakeResponse()

        def __exit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(
        "httpx.stream", lambda *args, **kwargs: FakeStream(*args, **kwargs)
    )

    source_url = (
        "https://instagram.fadb3-1.fna.fbcdn.net/o1/v/t2/f2/m86/video.mp4?_nc_sid=x"
    )
    result = provider._ingest_sync(source_url, "content-1")

    assert result.content_type == "video/mp4"
    assert result.size_bytes == 10
    headers = captured["kwargs"]["headers"]
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert headers["Referer"] == "https://www.instagram.com/"
    assert headers["Accept"].startswith("video/mp4")
