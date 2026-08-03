"""Metadata providers for enrichment.

The official Meta provider materializes metadata already embedded during discovery.
A chain of fallbacks (Meta Graph API and public yt-dlp extraction) can fill missing
``view_count``, ``video_duration``, ``like_count`` and other public fields when the
discovery payload is incomplete.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

from app.core.config import Settings
from app.core.rate_limit import GraphApiRateLimiter
from app.providers.scraper import NeedsInterventionError, _shortcode_to_media_id
from app.schemas.trends import ContentMetadata

logger = logging.getLogger(__name__)


class MetadataProvider(ABC):
    #: When True, the provider is skipped for Instagram-scraped (public web)
    #: content, where Graph API / yt-dlp are unreliable by design.
    skip_for_instagram_source: bool = False

    @abstractmethod
    async def fetch(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        raise NotImplementedError


class OfficialMetaMetadataProvider(MetadataProvider):
    """Provider that materializes metadata embedded by official Meta discovery."""

    async def fetch(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        if discovered_metadata:
            return metadata_from_dict(shortcode, discovered_metadata)
        raise KeyError(
            f"official Meta discovery metadata missing for {shortcode!r}; rediscover content"
        )


def metadata_from_dict(shortcode: str, row: dict[str, Any]) -> ContentMetadata:
    taken_at = row.get("taken_at")
    parsed_taken_at: datetime | None = None
    if isinstance(taken_at, str) and taken_at:
        parsed_taken_at = datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
    elif isinstance(taken_at, int | float):
        parsed_taken_at = datetime.fromtimestamp(taken_at, tz=UTC)
    return ContentMetadata(
        shortcode=shortcode,
        media_id=row.get("media_id") or row.get("source_id"),
        owner_username=row.get("owner_username"),
        owner_follower_count=int(row.get("owner_follower_count", 0) or 0),
        like_count=int(row.get("like_count", 0) or 0),
        comment_count=int(row.get("comment_count", 0) or 0),
        view_count=int(row.get("view_count", 0) or 0),
        share_count=int(row.get("share_count", 0) or 0),
        video_duration=(
            float(row["video_duration"]) if row.get("video_duration") is not None else None
        ),
        caption_text=row.get("caption_text", row.get("caption", "")),
        taken_at=parsed_taken_at,
        video_url=row.get("video_url"),
        thumbnail_url=row.get("thumbnail_url"),
        media_type=row.get("media_type"),
    )


async def _fetch_ytdlp_post(
    shortcode: str, *, request_timeout: float = 30.0
) -> dict[str, Any] | None:
    """Best-effort public metadata extraction for any Instagram post.

    Uses the generic ``/p/{shortcode}/`` permalink so photos, carousels, and
    reels are handled, and allows extraction to proceed even when the post has
    no downloadable video file. This covers "There is no video in this post"
    and "No video formats found" cases while still returning engagement.
    """
    import yt_dlp

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "format": "best[ext=mp4]/best",
        "extract_flat": False,
        "socket_timeout": request_timeout,
        "retries": 2,
        "ignore_no_formats_error": True,
    }

    def _extract_url(url: str) -> dict[str, Any] | None:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            return None
        if not info:
            return None

        result: dict[str, Any] = {}
        timestamp = info.get("timestamp")
        if timestamp:
            result["taken_at"] = int(timestamp)
        elif info.get("upload_date"):
            try:
                parsed = datetime.strptime(str(info["upload_date"]), "%Y%m%d").replace(
                    tzinfo=UTC
                )
                result["taken_at"] = int(parsed.timestamp())
            except Exception:
                pass

        if info.get("duration"):
            result["video_duration"] = float(info["duration"])
        if info.get("description"):
            result["caption_text"] = str(info["description"])
        if info.get("uploader"):
            result["owner_username"] = str(info["uploader"])
        if info.get("thumbnail"):
            result["thumbnail_url"] = str(info["thumbnail"])
        # Only store a video_url when there is an actual video (duration present),
        # so photos don't end up with an image URL in the video_url field.
        if info.get("url") and info.get("duration"):
            result["video_url"] = str(info["url"])
            result["media_type"] = "REELS"
        for key in ("like_count", "comment_count", "view_count"):
            value = info.get(key)
            if value is not None:
                result[key] = int(value)
        for key in ("channel_follower_count", "uploader_follower_count", "follower_count"):
            value = info.get(key)
            if value is not None:
                result["owner_follower_count"] = int(value)
                break
        return result if result else None

    try:
        # /p/ is the canonical shortcode permalink and redirects to /reel/ when
        # the post is a reel, so it is the most reliable first attempt.
        result = await asyncio.to_thread(_extract_url, f"https://www.instagram.com/p/{shortcode}/")
        if result:
            return result
        return await asyncio.to_thread(_extract_url, f"https://www.instagram.com/reel/{shortcode}/")
    except Exception:
        return None


class YtdlpMetadataProvider(MetadataProvider):
    """Public metadata fallback using yt-dlp for Instagram posts and reels."""

    skip_for_instagram_source = True

    async def fetch(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        row = await _fetch_ytdlp_post(shortcode)
        if row is None:
            raise KeyError(f"yt-dlp metadata unavailable for {shortcode!r}")
        return metadata_from_dict(shortcode, row)


class GraphApiMetadataProvider(MetadataProvider):
    """Graph API fallback for public Instagram media metadata.

    Prefers the ``media_id`` already discovered by the official Meta hashtag API or
    decoded from the shortcode, so it can call the Graph Media endpoint directly
    without relying on the brittle ``instagram_oembed`` product.  oEmbed is kept as
    a last resort for cases where no ``media_id`` is available.  Reads
    ``like_count``, ``comments_count``, ``view_count`` (Reels), ``caption``,
    ``timestamp``, ``media_url``, ``permalink`` and ``username``.  yt-dlp follows
    in the fallback chain for video duration and owner follower counts.
    """

    skip_for_instagram_source = True

    _MEDIA_FIELDS = (
        "timestamp,like_count,comments_count,view_count,caption,"
        "media_url,permalink,media_type,username"
    )

    def __init__(
        self,
        access_token: str,
        settings: Settings | None = None,
        *,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        self.access_token = access_token
        self.settings = settings
        self.graph_limiter = graph_limiter
        self.version = (
            settings.instagram_graph_api_version
            if settings
            else "v21.0"
        )

    async def fetch(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        row = await self._fetch_graph_api_post(shortcode, discovered_metadata)
        if row is None:
            # Return an incomplete record instead of raising so the fallback chain
            # (e.g. yt-dlp) can still attempt to fill missing fields.
            return ContentMetadata(shortcode=shortcode)
        return metadata_from_dict(shortcode, row)

    def _resolve_media_id(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None,
    ) -> str | None:
        """Return a media id from discovery metadata, shortcode decoding, or None."""
        if discovered_metadata:
            for key in ("source_id", "media_id", "id"):
                value = discovered_metadata.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        decoded = _shortcode_to_media_id(shortcode)
        if decoded is not None:
            return str(decoded)
        return None

    async def _resolve_media_id_via_oembed(
        self,
        shortcode: str,
        client: httpx.AsyncClient,
    ) -> str | None:
        """Resolve media_id through the Instagram oEmbed endpoint as a fallback."""
        for permalink in (
            f"https://www.instagram.com/reel/{shortcode}/",
            f"https://www.instagram.com/p/{shortcode}/",
        ):
            try:
                if self.graph_limiter is not None:
                    await self.graph_limiter.acquire()
                response = await client.get(
                    f"https://graph.facebook.com/{self.version}/instagram_oembed",
                    params={
                        "url": permalink,
                        "access_token": self.access_token,
                        "fields": "media_id,shortcode,author_name",
                    },
                )
                if response.is_success:
                    payload = response.json()
                    media_id = payload.get("media_id")
                    if media_id:
                        return str(media_id)
                else:
                    body = response.text[:200] if response.text else "empty"
                    logger.warning(
                        "instagram_oembed %s returned %s: %s",
                        permalink,
                        response.status_code,
                        body,
                    )
            except Exception as exc:  # noqa: BLE001 - network/oEmbed is best-effort
                logger.warning("instagram_oembed failed for %s: %s", permalink, exc)
        return None

    async def _fetch_graph_api_post(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                media_id = self._resolve_media_id(shortcode, discovered_metadata)
                if not media_id:
                    media_id = await self._resolve_media_id_via_oembed(shortcode, client)
                if not media_id:
                    logger.warning(
                        "Could not resolve media_id for shortcode %r; skipping Graph API",
                        shortcode,
                    )
                    return None

                if self.graph_limiter is not None:
                    await self.graph_limiter.acquire()
                media_resp = await client.get(
                    f"https://graph.facebook.com/{self.version}/{media_id}",
                    params={
                        "access_token": self.access_token,
                        "fields": self._MEDIA_FIELDS,
                    },
                )
                if not media_resp.is_success:
                    logger.warning(
                        "Graph API media %s returned %s: %s",
                        media_id,
                        media_resp.status_code,
                        media_resp.text[:200] if media_resp.text else "empty",
                    )
                    return None
                media = media_resp.json()

                taken_at = _parse_timestamp(media.get("timestamp"))
                if taken_at is None:
                    logger.warning(
                        "Graph API media %s response missing timestamp for %r",
                        media_id,
                        shortcode,
                    )
                    return None

                result: dict[str, Any] = {"taken_at": taken_at}
                if media.get("like_count") is not None:
                    result["like_count"] = int(media["like_count"])
                if media.get("comments_count") is not None:
                    result["comment_count"] = int(media["comments_count"])
                if media.get("view_count") is not None:
                    result["view_count"] = int(media["view_count"])
                if media.get("caption"):
                    result["caption_text"] = str(media["caption"])
                media_type = str(media.get("media_type") or "").upper()
                if media_type:
                    result["media_type"] = media_type
                media_url = media.get("media_url")
                if media_url:
                    if media_type in {"VIDEO", "REELS"}:
                        result["video_url"] = str(media_url)
                    else:
                        result["thumbnail_url"] = str(media_url)
                if media.get("permalink"):
                    result["permalink"] = str(media["permalink"])
                if media.get("username"):
                    result["owner_username"] = str(media["username"])
                return result if result else None
        except Exception as exc:  # noqa: BLE001 - Graph API fallback is best-effort
            logger.warning("Graph API metadata fetch failed for %r: %s", shortcode, exc)
            return None


def _parse_timestamp(value: Any) -> int | None:
    """Convert an int, float, digit-string, or ISO 8601 string to a Unix timestamp."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            return int(cleaned)
        if cleaned:
            try:
                dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
                return int(dt.timestamp())
            except ValueError:
                pass
    return None


def _is_incomplete(metadata: ContentMetadata) -> bool:
    """Return True when core metadata is missing and an upstream provider may fill it.

    Photos/carousels naturally have no video_duration or view_count, and public
    scraping often cannot read owner_follower_count, so those are no longer
    treated as incomplete by themselves. Fallbacks are requested when the publish
    timestamp, author, or a video's duration/view count is missing.
    """
    if metadata.taken_at is None:
        return True
    if not metadata.owner_username:
        return True
    if metadata.media_type in {"REELS", "VIDEO"} or metadata.video_url is not None:
        if metadata.video_duration is None:
            return True
        if metadata.video_url is not None and metadata.view_count == 0:
            return True
    # Tag/feed discovery nodes carry no media type, video URL, or view counts;
    # the authenticated session provider can complete them per post.
    if (
        metadata.media_type is None
        and metadata.video_url is None
        and metadata.view_count == 0
    ):
        return True
    return False


def _is_better(fallback: ContentMetadata, base: ContentMetadata) -> bool:
    """Return True when ``fallback`` provides any strictly better field."""
    for field_name in ContentMetadata.model_fields:
        fallback_value = getattr(fallback, field_name)
        if fallback_value in (None, "", 0):
            continue
        base_value = getattr(base, field_name)
        if base_value in (None, "", 0):
            return True
        if isinstance(fallback_value, (int, float)) and fallback_value > base_value:
            return True
    return False


def _merge_content_metadata(base: ContentMetadata, update: ContentMetadata) -> ContentMetadata:
    """Fill missing/empty fields in ``base`` with values from ``update``."""
    updates: dict[str, Any] = {}
    for field_name in ContentMetadata.model_fields:
        fallback_value = getattr(update, field_name)
        if fallback_value in (None, "", 0):
            continue
        base_value = getattr(base, field_name)
        if base_value in (None, "", 0):
            updates[field_name] = fallback_value
    return base.model_copy(update=updates) if updates else base


def _is_instagram_scraped_source(
    discovered_metadata: dict[str, Any] | None, context: dict[str, Any] | None
) -> bool:
    """Detect when the discovered record came from the Playwright Instagram scraper.

    The Meta Graph API and yt-dlp fallbacks are not reliable for arbitrary public
    posts scraped from instagram.com, so we skip them for that provenance.
    """
    source = (discovered_metadata or {}).get("source") or (context or {}).get("source")
    return isinstance(source, str) and source == "instagram"


class ChainedMetadataProvider(MetadataProvider):
    """Try the primary provider and fall back when metadata is incomplete.

    The fallback is triggered whenever ``context`` is provided (enrichment always
    passes context) so that missing fields are filled on the first pass, not only
    on zero-score retries.
    """

    def __init__(self, primary: MetadataProvider, fallback: MetadataProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def skip_for_instagram_source(self) -> bool:  # type: ignore[override]
        return bool(
            getattr(self.primary, "skip_for_instagram_source", False)
            and getattr(self.fallback, "skip_for_instagram_source", False)
        )

    async def fetch(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        metadata = await self.primary.fetch(shortcode, discovered_metadata, context=context)
        if context is not None and _is_incomplete(metadata):
            if _is_instagram_scraped_source(discovered_metadata, context) and getattr(
                self.fallback, "skip_for_instagram_source", False
            ):
                logger.debug(
                    "Skipping metadata fallbacks for Instagram-scraped post %r", shortcode
                )
                return metadata
            try:
                fallback_metadata = await self.fallback.fetch(
                    shortcode, discovered_metadata, context=context
                )
            except NeedsInterventionError:
                raise
            except Exception:
                fallback_metadata = None
            if fallback_metadata is not None and _is_better(fallback_metadata, metadata):
                metadata = _merge_content_metadata(metadata, fallback_metadata)
        return metadata


class FixtureMetadataProvider(MetadataProvider):
    """JSON-fixture metadata provider for local development and tests (no network)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def fetch(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        rows = json.loads(self.path.read_text()) if self.path.exists() else []
        row = next((item for item in rows if item["shortcode"] == shortcode), None)
        if row is None:
            raise KeyError(f"no fixture metadata for shortcode {shortcode!r}")
        return metadata_from_dict(shortcode, row)


def _build_fallback_provider(
    settings: Settings,
    access_token: str | None,
    *,
    graph_limiter: GraphApiRateLimiter | None = None,
) -> MetadataProvider | None:
    providers: list[MetadataProvider] = []
    if access_token and settings.metadata_fallback_provider in ("graph", "all"):
        providers.append(
            GraphApiMetadataProvider(access_token, settings, graph_limiter=graph_limiter)
        )
    if settings.metadata_fallback_provider in ("ytdlp", "all"):
        providers.append(YtdlpMetadataProvider())
    if not providers:
        return None
    chain = providers[0]
    for provider in providers[1:]:
        chain = ChainedMetadataProvider(chain, provider)
    return chain


def build_metadata_provider(
    settings: Settings | None = None,
    *,
    access_token: str | None = None,
    redis: Any | None = None,
) -> MetadataProvider:
    from app.core.rate_limit import build_graph_rate_limiter

    graph_limiter = (
        build_graph_rate_limiter(redis, settings) if settings else None
    )
    if settings and settings.scraper_adapter == "fixture":
        return FixtureMetadataProvider(settings.metadata_fixture_path)
    official: MetadataProvider = OfficialMetaMetadataProvider()
    if settings and settings.scraper_adapter == "instagram":
        from app.providers.scraper import InstagramSessionMetadataProvider

        official = ChainedMetadataProvider(
            official,
            cast(MetadataProvider, InstagramSessionMetadataProvider(settings)),
        )
    fallback = _build_fallback_provider(
        settings or Settings(), access_token, graph_limiter=graph_limiter
    )
    if fallback is not None:
        return ChainedMetadataProvider(official, fallback)
    return official
