"""AWS Transcribe speech-to-text provider."""

from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.core.errors import TransientError, is_throttling_error
from app.schemas.trends import TranscriptResult


class TranscriptionError(RuntimeError):
    pass


_DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _download_headers(url: str) -> dict[str, str]:
    """Return browser-like headers for Instagram CDN URLs.

    Signed Instagram/Facebook CDN links often reject requests without a realistic
    User-Agent and Referer, returning HTTP 403.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if "instagram.com" in hostname or "fbcdn.net" in hostname:
        return {
            "User-Agent": _DOWNLOAD_USER_AGENT,
            "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
            "Referer": "https://www.instagram.com/",
        }
    return {}


class TranscriptionProvider(ABC):
    @abstractmethod
    async def transcribe(self, shortcode: str, video_url: str | None) -> TranscriptResult:
        raise NotImplementedError


class AwsTranscribeProvider(TranscriptionProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.transcribe_s3_bucket:
            raise TranscriptionError("transcribe_s3_bucket must be configured for AWS Transcribe")
        self.bucket = settings.transcribe_s3_bucket

    async def transcribe(self, shortcode: str, video_url: str | None) -> TranscriptResult:
        if not video_url:
            return TranscriptResult(text="", language=None)
        return await asyncio.to_thread(self._transcribe_sync, shortcode, video_url)

    def _transcribe_sync(self, shortcode: str, video_url: str) -> TranscriptResult:
        import boto3
        from botocore.config import Config  # type: ignore[import-untyped]
        from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                video_path = tmp_path / "video.mp4"
                audio_path = tmp_path / "audio.mp3"
                self._download(video_url, video_path)
                if not self._extract_audio(video_path, audio_path):
                    return TranscriptResult(text="", language=None)

                job_name = f"involo-{shortcode}-{uuid.uuid4().hex[:8]}"
                key = (
                    f"{self.settings.media_s3_prefix}/{shortcode}/transcription/audio.mp3"
                )
                s3_options: dict[str, Any] = {
                    "region_name": self.settings.aws_region,
                    "config": Config(s3={"addressing_style": "path"}),
                }
                if self.settings.transcribe_s3_endpoint_url:
                    s3_options["endpoint_url"] = self.settings.transcribe_s3_endpoint_url
                if self.settings.transcribe_s3_access_key_id:
                    s3_options["aws_access_key_id"] = self.settings.transcribe_s3_access_key_id
                if self.settings.transcribe_s3_secret_access_key:
                    s3_options["aws_secret_access_key"] = (
                        self.settings.transcribe_s3_secret_access_key.get_secret_value()
                    )
                if not self.bucket or not key:
                    raise TranscriptionError(
                        f"Invalid S3 bucket or key for transcription: "
                        f"bucket={self.bucket!r}, key={key!r}"
                    )

                s3 = boto3.client("s3", **s3_options)
                s3.upload_file(str(audio_path), self.bucket, key)

                transcribe = boto3.client("transcribe", region_name=self.settings.aws_region)
                try:
                    transcribe.start_transcription_job(
                        TranscriptionJobName=job_name,
                        Media={"MediaFileUri": f"s3://{self.bucket}/{key}"},
                        MediaFormat="mp3",
                        IdentifyLanguage=True,
                    )
                except ClientError as exc:
                    error_code = exc.response.get("Error", {}).get("Code", "Unknown")
                    raise TranscriptionError(
                        f"AWS Transcribe {error_code}: {exc}"
                    ) from exc
                try:
                    result = self._poll(transcribe, job_name)
                finally:
                    s3.delete_object(Bucket=self.bucket, Key=key)
                return result
        except ClientError as exc:
            if is_throttling_error(exc):
                raise TransientError(f"AWS Transcribe throttled: {exc}") from exc
            raise
        except BotoCoreError as exc:
            raise TransientError(f"AWS Transcribe transport error: {exc}") from exc

    def _download(self, url: str, target: Path) -> None:
        try:
            with httpx.stream(
                "GET", url, headers=_download_headers(url), timeout=60.0, follow_redirects=True
            ) as response:
                transient = self._transient(response)
                if transient is not None:
                    raise transient
                response.raise_for_status()
                with target.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            raise TransientError(f"video download failed: {exc}") from exc

    @staticmethod
    def _transient(response: httpx.Response) -> TransientError | None:
        from app.core.errors import transient_from_response

        return transient_from_response(response)

    def _extract_audio(self, source: Path, target: Path) -> bool:
        """Extract audio; return False when the video has no audio stream."""
        import subprocess

        completed = subprocess.run(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-i",
                str(source),
                "-vn",
                "-acodec",
                "libmp3lame",
                str(target),
            ],
            capture_output=True,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode(errors="ignore")
            if "does not contain any stream" in stderr:
                # Videos without an audio track cannot be transcribed; this is
                # not a failure of the content item.
                return False
            # The actionable ffmpeg error is at the end of stderr; the head is
            # just the build banner.
            raise TranscriptionError(f"ffmpeg failed: {stderr[-500:]}")
        return True

    def _poll(self, transcribe: Any, job_name: str) -> TranscriptResult:
        deadline = time.monotonic() + self.settings.transcribe_timeout_seconds
        while True:
            job = transcribe.get_transcription_job(TranscriptionJobName=job_name)[
                "TranscriptionJob"
            ]
            status = job["TranscriptionJobStatus"]
            if status == "COMPLETED":
                uri = job["Transcript"]["TranscriptFileUri"]
                language = job.get("LanguageCode")
                text = self._fetch_transcript_text(uri)
                return TranscriptResult(text=text, language=language)
            if status == "FAILED":
                raise TranscriptionError(job.get("FailureReason", "transcription job failed"))
            if time.monotonic() > deadline:
                raise TranscriptionError("transcription job timed out")
            time.sleep(self.settings.transcribe_poll_seconds)

    def _fetch_transcript_text(self, uri: str) -> str:
        response = httpx.get(uri, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
        transcripts = payload.get("results", {}).get("transcripts", [])
        return " ".join(item.get("transcript", "") for item in transcripts).strip()


class NoOpTranscriptionProvider(TranscriptionProvider):
    """Returns an empty transcript without calling AWS Transcribe.

    Useful for local development and tests where an S3-compatible store is used
    but AWS Transcribe is unavailable.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(self, shortcode: str, video_url: str | None) -> TranscriptResult:
        return TranscriptResult(text="", language=None)


def build_transcription_provider(settings: Settings) -> TranscriptionProvider:
    if settings.transcription_provider == "fake":
        return NoOpTranscriptionProvider(settings)
    if settings.transcription_provider == "aws":
        return AwsTranscribeProvider(settings)
    raise TranscriptionError(f"unknown transcription_provider: {settings.transcription_provider}")
