"""Bounded media download, S3 persistence, and ffmpeg keyframe extraction."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.core.errors import TransientError, is_throttling_error


class MediaProcessingError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class StoredMedia:
    bucket: str
    key: str
    uri: str
    content_type: str
    sha256: str
    size_bytes: int
    region: str = ""


@dataclass(frozen=True)
class Keyframe:
    offset_seconds: float
    media: StoredMedia


@dataclass(frozen=True)
class VideoSegment:
    start_seconds: float
    end_seconds: float
    media: StoredMedia


class _MediaKeyBuilder:
    """Deterministic, content-centric S3 keys for media artifacts."""

    @staticmethod
    def original(content_id: str, suffix: str, prefix: str) -> str:
        return f"{prefix}/{content_id}/media/original{suffix}"

    @staticmethod
    def keyframe(content_id: str, offset_ms: int, prefix: str) -> str:
        return f"{prefix}/{content_id}/keyframes/{offset_ms:010d}.jpg"

    @staticmethod
    def segment(content_id: str, index: int, prefix: str) -> str:
        return f"{prefix}/{content_id}/segments/{index:05d}.mp4"

    @staticmethod
    def embedding_mirror(source_key: str, prefix: str) -> str:
        """Mirror a media key into the embedding subtree while keeping content_id."""
        if source_key.startswith(prefix + "/"):
            relative = source_key[len(prefix) + 1 :]
            parts = relative.split("/", 2)
            if len(parts) >= 2:
                content_id = parts[0]
                subpath = "/".join(parts[1:])
                return f"{prefix}/{content_id}/embedding/{subpath}"
        # Fallback for flat or un-prefixed keys.
        relative = source_key.removeprefix(prefix + "/").lstrip("/")
        return f"{prefix}/embedding/{relative}"


class MediaProvider(ABC):
    """Abstract base for media download, keyframe extraction, and segmentation."""

    @abstractmethod
    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        raise NotImplementedError

    @abstractmethod
    async def extract_keyframes(
        self, media: StoredMedia, content_id: str, offsets: list[float]
    ) -> list[Keyframe]:
        raise NotImplementedError

    @abstractmethod
    async def segment_video(
        self, media: StoredMedia, content_id: str, segment_seconds: int
    ) -> list[VideoSegment]:
        raise NotImplementedError

    @abstractmethod
    async def delete_keys(self, keys: list[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def prepare_embedding_media(self, media: StoredMedia) -> StoredMedia:
        raise NotImplementedError

    @abstractmethod
    async def delete_assets(self, assets: list[dict[str, str]]) -> None:
        raise NotImplementedError

    def public_url(self, media: StoredMedia) -> str:
        """Return a client-fetchable HTTPS URL for the stored media.

        Subclasses may override this to generate presigned or public URLs.
        The default returns the stored URI unchanged.
        """
        return media.uri


class S3MediaProvider(MediaProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.media_s3_bucket:
            raise MediaProcessingError("media S3 bucket is not configured")
        self.settings = settings
        self.bucket = settings.media_s3_bucket

    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        return await asyncio.to_thread(self._ingest_sync, source_url, content_id)

    async def extract_keyframes(
        self, media: StoredMedia, content_id: str, offsets: list[float]
    ) -> list[Keyframe]:
        return await asyncio.to_thread(
            self._extract_keyframes_sync, media, content_id, sorted(set(offsets))
        )

    async def segment_video(
        self, media: StoredMedia, content_id: str, segment_seconds: int
    ) -> list[VideoSegment]:
        return await asyncio.to_thread(
            self._segment_video_sync, media, content_id, segment_seconds
        )

    async def delete_keys(self, keys: list[str]) -> None:
        unique = sorted(set(key for key in keys if key))
        for start in range(0, len(unique), 1000):
            await asyncio.to_thread(self._delete_keys_sync, unique[start : start + 1000])

    async def prepare_embedding_media(self, media: StoredMedia) -> StoredMedia:
        return await asyncio.to_thread(self._prepare_embedding_media_sync, media)

    async def delete_assets(self, assets: list[dict[str, str]]) -> None:
        grouped: dict[str, set[str]] = {}
        for asset in assets:
            bucket, key = asset.get("bucket"), asset.get("key")
            if bucket and key:
                grouped.setdefault(bucket, set()).add(key)
        for bucket, keys in grouped.items():
            ordered = sorted(keys)
            for start in range(0, len(ordered), 1000):
                await asyncio.to_thread(
                    self._delete_assets_sync,
                    bucket,
                    ordered[start : start + 1000],
                )

    def public_url(self, media: StoredMedia) -> str:
        """Generate a presigned GET URL for the stored media object.

        For AWS S3 the expiry is capped at 7 days. For S3-compatible services
        configured through a custom endpoint the configured media retention
        period is used.
        """
        expires_in = self.settings.media_retention_days * 86400
        if not self.settings.transcribe_s3_endpoint_url:
            expires_in = min(expires_in, 7 * 86400)
        return cast(
            str,
            self._client().generate_presigned_url(
                "get_object",
                Params={"Bucket": media.bucket, "Key": media.key},
                ExpiresIn=expires_in,
            ),
        )

    def _client(self) -> Any:
        return self._s3_client(
            self.settings.media_s3_region,
            self.settings.transcribe_s3_endpoint_url,
        )

    def _embedding_client(self) -> Any:
        return self._s3_client(
            self.settings.embedding_media_s3_region,
            self.settings.embedding_media_s3_endpoint_url,
        )

    def _s3_client(self, region: str, endpoint_url: str | None) -> Any:
        import boto3

        options: dict[str, Any] = {"region_name": region}
        if endpoint_url:
            options["endpoint_url"] = endpoint_url
        if endpoint_url and self.settings.transcribe_s3_access_key_id:
            options["aws_access_key_id"] = self.settings.transcribe_s3_access_key_id
        if endpoint_url and self.settings.transcribe_s3_secret_access_key:
            options["aws_secret_access_key"] = (
                self.settings.transcribe_s3_secret_access_key.get_secret_value()
            )
        return boto3.client("s3", **options)

    def _ingest_sync(self, source_url: str, content_id: str) -> StoredMedia:
        parsed = urlparse(source_url)
        if parsed.scheme not in {"https", "http"}:
            raise MediaProcessingError("media URL must use HTTP(S)")
        digest = hashlib.sha256()
        size = 0
        suffix = Path(parsed.path).suffix[:10] or ".mp4"
        key = _MediaKeyBuilder.original(
            content_id, suffix, self.settings.media_s3_prefix
        )
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
            try:
                with httpx.stream(
                    "GET",
                    source_url,
                    headers=_download_headers(source_url),
                    timeout=60.0,
                    follow_redirects=True,
                ) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.settings.media_max_download_bytes:
                            raise MediaProcessingError("media exceeds configured size limit")
                        digest.update(chunk)
                        temporary.write(chunk)
            except httpx.HTTPError as exc:
                raise TransientError("media download failed") from exc
            temporary.flush()
            content_type = mimetypes.guess_type(suffix)[0] or "video/mp4"
            self._upload(temporary.name, key, content_type)
        return StoredMedia(
            bucket=self.bucket,
            key=key,
            uri=f"s3://{self.bucket}/{key}",
            content_type=content_type,
            sha256=digest.hexdigest(),
            size_bytes=size,
            region=self.settings.media_s3_region,
        )

    def _extract_keyframes_sync(
        self, media: StoredMedia, content_id: str, offsets: list[float]
    ) -> list[Keyframe]:
        """
        Extract keyframes from a media file.

        Args:
            media: The media file to extract keyframes from.
            content_id: The ID of the content to extract keyframes from.
            offsets: The offsets to extract keyframes from.

        Returns:
            list[Keyframe]: The extracted keyframes.
        """
        client = self._client()
        frames: list[Keyframe] = []
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            client.download_file(media.bucket, media.key, str(source))
            for index, offset in enumerate(offsets):
                output = Path(directory) / f"frame-{index:03d}.jpg"
                command = [
                    self.settings.ffmpeg_binary,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    str(offset),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                    str(output),
                ]
                try:
                    subprocess.run(command, check=True, capture_output=True, timeout=60)
                except subprocess.CalledProcessError:
                    # A seek beyond a short video's duration produces no frame.
                    continue
                except subprocess.TimeoutExpired as exc:
                    raise MediaProcessingError("ffmpeg keyframe extraction timed out") from exc
                except OSError as exc:
                    raise MediaProcessingError("ffmpeg keyframe extraction failed") from exc
                if not output.exists() or output.stat().st_size == 0:
                    continue
                key = _MediaKeyBuilder.keyframe(
                    content_id,
                    int(offset * 1000),
                    self.settings.media_s3_prefix,
                )
                self._upload(str(output), key, "image/jpeg")
                data = output.read_bytes()
                stored = StoredMedia(
                    bucket=self.bucket,
                    key=key,
                    uri=f"s3://{self.bucket}/{key}",
                    content_type="image/jpeg",
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                    region=self.settings.media_s3_region,
                )
                frames.append(Keyframe(offset_seconds=offset, media=stored))
        return frames

    def _segment_video_sync(
        self, media: StoredMedia, content_id: str, segment_seconds: int
    ) -> list[VideoSegment]:
        client = self._client()
        segments: list[VideoSegment] = []
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            client.download_file(media.bucket, media.key, str(source))
            pattern = Path(directory) / "segment-%05d.mp4"
            command = [
                self.settings.ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-fflags",
                "+discardcorrupt",
                "-err_detect",
                "ignore_err",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-force_key_frames",
                f"expr:gte(t,n_forced*{segment_seconds})",
                "-f",
                "segment",
                "-segment_time",
                str(segment_seconds),
                "-reset_timestamps",
                "1",
                "-y",
                str(pattern),
            ]
            try:
                result = subprocess.run(command, check=False, capture_output=True, timeout=900)
            except (subprocess.SubprocessError, OSError) as exc:
                raise MediaProcessingError(f"video segmentation failed: {exc}") from exc
            if result.returncode != 0:
                stderr = (
                    result.stderr.decode(errors="replace").strip()[:2000]
                    if result.stderr
                    else ""
                )
                logger.error("ffmpeg segmentation failed: %s", stderr)
                raise MediaProcessingError(f"video segmentation failed: {stderr[:500]}")
            start = 0.0
            for index, path in enumerate(sorted(Path(directory).glob("segment-*.mp4"))):
                duration = self._probe_duration(path)
                if duration <= 0:
                    continue
                key = _MediaKeyBuilder.segment(
                    content_id,
                    index,
                    self.settings.media_s3_prefix,
                )
                self._upload(str(path), key, "video/mp4")
                data = path.read_bytes()
                stored = StoredMedia(
                    bucket=self.bucket,
                    key=key,
                    uri=f"s3://{self.bucket}/{key}",
                    content_type="video/mp4",
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                    region=self.settings.media_s3_region,
                )
                end = start + min(duration, float(segment_seconds))
                segments.append(VideoSegment(start, end, stored))
                start = end
        if not segments:
            raise MediaProcessingError("ffmpeg produced no video segments")
        return segments

    def _prepare_embedding_media_sync(self, media: StoredMedia) -> StoredMedia:
        target_bucket = self.settings.embedding_media_s3_bucket
        if not target_bucket:
            if self.settings.media_s3_region != self.settings.bedrock_embedding_region:
                raise MediaProcessingError(
                    "embedding media bucket is required for cross-region processing"
                )
            return media
        if (
            media.bucket == target_bucket
            and self.settings.embedding_media_s3_region
            == self.settings.media_s3_region
        ):
            return media
        suffix = Path(media.key).suffix
        key = _MediaKeyBuilder.embedding_mirror(
            media.key, self.settings.media_s3_prefix
        )
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
            self._client().download_file(media.bucket, media.key, temporary.name)
            self._upload_to(
                self._embedding_client(),
                target_bucket,
                temporary.name,
                key,
                media.content_type,
                use_server_side_encryption=self.settings.embedding_media_s3_endpoint_url is None,
            )
        return StoredMedia(
            bucket=target_bucket,
            key=key,
            uri=f"s3://{target_bucket}/{key}",
            content_type=media.content_type,
            sha256=media.sha256,
            size_bytes=media.size_bytes,
            region=self.settings.embedding_media_s3_region,
        )

    def _probe_duration(self, path: Path) -> float:
        command = [
            self.settings.ffprobe_binary,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            return float(result.stdout.strip())
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise MediaProcessingError("unable to determine segment duration") from exc

    def _upload(self, filename: str, key: str, content_type: str) -> None:
        self._upload_to(
            self._client(),
            self.bucket,
            filename,
            key,
            content_type,
            use_server_side_encryption=self.settings.transcribe_s3_endpoint_url is None,
        )

    def _upload_to(
        self,
        client: Any,
        bucket: str,
        filename: str,
        key: str,
        content_type: str,
        use_server_side_encryption: bool = True,
    ) -> None:
        from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

        retention_until = datetime.now(UTC) + timedelta(days=self.settings.media_retention_days)
        extra_args: dict[str, Any] = {
            "ContentType": content_type,
            "Tagging": f"retention-days={self.settings.media_retention_days}",
            "Metadata": {
                "managed-by": "involo",
                "retention-until": retention_until.isoformat(),
            },
        }
        if use_server_side_encryption:
            extra_args["ServerSideEncryption"] = "AES256"
        try:
            client.upload_file(
                filename,
                bucket,
                key,
                ExtraArgs=extra_args,
            )
        except ClientError as exc:
            if is_throttling_error(exc):
                raise TransientError("S3 media upload throttled") from exc
            raise MediaProcessingError("S3 media upload failed") from exc
        except BotoCoreError as exc:
            raise TransientError("S3 media transport failure") from exc

    def _delete_keys_sync(self, keys: list[str]) -> None:
        self._delete_assets_sync(self.bucket, keys)

    def _delete_assets_sync(self, bucket: str, keys: list[str]) -> None:
        if not keys:
            return
        try:
            client = (
                self._embedding_client()
                if bucket == self.settings.embedding_media_s3_bucket
                else self._client()
            )
            result = client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
            )
        except Exception as exc:  # noqa: BLE001
            raise MediaProcessingError("S3 user-media erasure failed") from exc
        if result.get("Errors"):
            raise MediaProcessingError("S3 user-media erasure was incomplete")


class NoOpMediaProvider(MediaProvider):
    """Stub media provider for local/fake pipeline runs.

    Avoids expensive downloads, ffmpeg, and S3 uploads while still producing the
    StoredMedia/Keyframe/VideoSegment objects the multimodal service expects.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        key = _MediaKeyBuilder.original(
            content_id, ".mp4", self.settings.media_s3_prefix
        )
        bucket = self.settings.media_s3_bucket or "noop"
        return StoredMedia(
            bucket=bucket,
            key=key,
            uri=f"s3://{bucket}/{key}",
            content_type="video/mp4",
            sha256=hashlib.sha256(source_url.encode()).hexdigest(),
            size_bytes=0,
            region=self.settings.media_s3_region,
        )

    async def extract_keyframes(
        self, media: StoredMedia, content_id: str, offsets: list[float]
    ) -> list[Keyframe]:
        if not offsets:
            return []
        return [Keyframe(offset_seconds=0.0, media=media)]

    async def segment_video(
        self, media: StoredMedia, content_id: str, segment_seconds: int
    ) -> list[VideoSegment]:
        return [
            VideoSegment(
                start_seconds=0.0,
                end_seconds=float(segment_seconds),
                media=media,
            )
        ]

    async def delete_keys(self, keys: list[str]) -> None:
        return None

    async def prepare_embedding_media(self, media: StoredMedia) -> StoredMedia:
        return media

    async def delete_assets(self, assets: list[dict[str, str]]) -> None:
        return None

    def public_url(self, media: StoredMedia) -> str:
        return f"https://example.com/{media.key.lstrip('/')}"


def build_media_provider(settings: Settings) -> MediaProvider:
    if settings.media_provider == "noop":
        return NoOpMediaProvider(settings)
    return S3MediaProvider(settings)
