from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.providers.transcription import (
    AwsTranscribeProvider,
    NoOpTranscriptionProvider,
    TranscriptionError,
    build_transcription_provider,
)


@pytest.mark.asyncio
async def test_no_op_transcription_provider_returns_empty_result() -> None:
    provider = NoOpTranscriptionProvider(Settings())
    result = await provider.transcribe("shortcode", "https://example.test/video.mp4")
    assert result.text == ""
    assert result.language is None


def test_build_transcription_provider_returns_aws_when_configured() -> None:
    settings = Settings(transcription_provider="aws", transcribe_s3_bucket="involo-transcribe")
    provider = build_transcription_provider(settings)
    assert isinstance(provider, AwsTranscribeProvider)


def test_build_transcription_provider_returns_no_op_when_fake() -> None:
    settings = Settings(transcription_provider="fake")
    provider = build_transcription_provider(settings)
    assert isinstance(provider, NoOpTranscriptionProvider)


def test_build_transcription_provider_exposes_settings_attribute() -> None:
    settings = Settings(transcription_provider="fake")
    provider = build_transcription_provider(settings)
    assert provider.settings is settings


def test_build_transcription_provider_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(transcription_provider="fake")
    monkeypatch.setattr(settings, "transcription_provider", "unknown")
    with pytest.raises(TranscriptionError, match="unknown transcription_provider"):
        build_transcription_provider(settings)


def test_build_transcription_provider_defaults_to_aws() -> None:
    settings = Settings(transcribe_s3_bucket="involo-transcribe")
    provider = build_transcription_provider(settings)
    assert isinstance(provider, AwsTranscribeProvider)


def test_aws_download_sends_browser_headers_for_instagram_cdn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = AwsTranscribeProvider(Settings(transcribe_s3_bucket="involo-transcribe"))
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> Any:
            yield b"video-bytes"

    class FakeStream:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __enter__(self) -> FakeResponse:
            return FakeResponse()

        def __exit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(
        "httpx.stream", lambda *args, **kwargs: FakeStream(*args, **kwargs)
    )

    target = tmp_path / "video.mp4"
    provider._download(
        "https://instagram.fadb3-1.fna.fbcdn.net/video.mp4", target
    )

    assert target.read_bytes() == b"video-bytes"
    headers = captured["headers"]
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert headers["Referer"] == "https://www.instagram.com/"


@pytest.mark.asyncio
async def test_video_without_audio_stream_returns_empty_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = AwsTranscribeProvider(Settings(transcribe_s3_bucket="involo-transcribe"))
    monkeypatch.setattr(provider, "_download", lambda url, target: target.write_bytes(b"v"))

    class Completed:
        returncode = 1
        stderr = b"banner\nOutput #0, mp3, to 'x':\nOutput file #0 does not contain any stream"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: Completed())

    result = await provider.transcribe("shortcode", "https://example.test/video.mp4")
    assert result.text == ""
    assert result.language is None


def test_extract_audio_raises_actionable_error_for_other_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = AwsTranscribeProvider(Settings(transcribe_s3_bucket="involo-transcribe"))

    class Completed:
        returncode = 1
        stderr = b"banner" * 200 + b"\nreal codec error at the end"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: Completed())

    with pytest.raises(TranscriptionError, match="real codec error at the end"):
        provider._extract_audio(tmp_path / "v.mp4", tmp_path / "a.mp3")
