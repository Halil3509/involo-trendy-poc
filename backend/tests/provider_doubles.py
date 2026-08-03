"""Network-free provider doubles. Never imported by the application package."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import numpy as np
from qdrant_client import models

from app.providers.brand_analysis import BrandAnalysisProvider
from app.providers.brand_pdf import BrandAnalysisPdfProvider
from app.providers.embedding import EmbeddingProvider
from app.providers.instagram_profile import (
    InstagramAccount,
    InstagramAudience,
    InstagramGraphError,
    InstagramMedia,
    InstagramNeedsReauth,
    InstagramProfileProvider,
    TokenBundle,
)
from app.providers.media import Keyframe, StoredMedia, VideoSegment
from app.providers.metadata import MetadataProvider, metadata_from_dict
from app.providers.profile_summary import ProfileSummaryContext, ProfileSummaryProvider
from app.providers.recommendations import (
    RecommendationContext,
    RecommendationProvider,
    RecommendationProviderResult,
    TrendContext,
)
from app.providers.scraper import EmitFn, ScraperAdapter, noop_emit, parse_instagram_url
from app.providers.transcription import TranscriptionProvider
from app.schemas.brand_analysis import (
    BrandAnalysisPdf,
    BrandAnalysisPost,
    BrandAnalysisReport,
    BrandAnalysisReportContext,
    CaptionAnalysis,
)
from app.schemas.intelligence import VisualAnalysis
from app.schemas.recommendations import RecommendationCard, RecommendationUsage
from app.schemas.trends import ContentMetadata, ScrapedItem, TranscriptResult
from app.services.multimodal import MultimodalResult, fuse_vectors


class FakeEmbeddingProvider(EmbeddingProvider):
    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        generator = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        vector = generator.standard_normal(self.vector_size)
        norm = float(np.linalg.norm(vector))
        return [float(value) for value in (vector / norm if norm else vector)]

    async def embed_media(self, s3_uri: str, *, purpose: str = "GENERIC_INDEX") -> list[float]:
        return await self.embed(f"{purpose}:{s3_uri}")


class FakeMediaProvider:
    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        return self._stored(f"media/{content_id}.mp4", "video/mp4")

    async def extract_keyframes(
        self, media: StoredMedia, content_id: str, offsets: list[float]
    ) -> list[Keyframe]:
        return [Keyframe(0, self._stored(f"frames/{content_id}.jpg", "image/jpeg"))]

    async def segment_video(
        self, media: StoredMedia, content_id: str, segment_seconds: int
    ) -> list[VideoSegment]:
        return [
            VideoSegment(
                0,
                float(segment_seconds),
                self._stored(f"segments/{content_id}.mp4", "video/mp4"),
            )
        ]

    async def prepare_embedding_media(self, media: StoredMedia) -> StoredMedia:
        return media

    def public_url(self, media: StoredMedia) -> str:
        return f"https://example.com/{media.key}"

    @staticmethod
    def _stored(key: str, content_type: str) -> StoredMedia:
        return StoredMedia("test", key, f"s3://test/{key}", content_type, key, 10)


class FakeVisionProvider:
    async def analyze(
        self, media: StoredMedia, keyframes: list[Keyframe], *, caption: str
    ) -> VisualAnalysis:
        return VisualAnalysis(
            opening_frame=f"Opening frame for {caption[:40]}",
            visual_signature=["creator", "close-up"],
            color_palette=["soft neutral", "warm beige"],
            lighting_type="natural_window_light",
            texture_descriptors=["matte_skin", "cream_swipe"],
            shooting_angle="45_degree",
            aesthetic_style="clean_editorial",
            composition_style="product_centered",
            asmr_elements=["cream_spread"],
            contextual_placement=(
                "product rests on a marble vanity beside a soft towel, "
                "framed as a morning ritual"
            ),
            sensory_visual_proof=["cream_swipe", "dewy_skin", "matte_finish"],
            aspirational_lifestyle_narrative=(
                "a calm morning ritual for a woman who treats skincare as self-respect"
            ),
            visual_hook="a fresh cream swatch about to be massaged into dewy skin",
            material_context="frosted glass jar on pale stone with natural window light",
            confidence=1,
        )


class FakeMultimodalProcessor:
    def __init__(
        self, qdrant: Any, embedding: EmbeddingProvider, vector_schema_version: str
    ) -> None:
        self.qdrant = qdrant
        self.embedding = embedding
        self.vector_schema_version = vector_schema_version

    async def process_asset(
        self,
        *,
        source_url: str,
        content_id: str,
        caption: str,
        combined_text: str,
        collection: str,
        payload: dict[str, Any],
    ) -> MultimodalResult:
        text = await self.embedding.embed(combined_text)
        media = await self.embedding.embed_media(f"s3://test/{content_id}.mp4")
        fused = fuse_vectors(text, media, text_weight=0.5, media_weight=0.5)
        point_id = hashlib.sha256(content_id.encode()).hexdigest()[:32]
        await self.qdrant.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={"text": text, "audio_video": media, "fused": fused},
                    payload={**payload, "schema_version": self.vector_schema_version},
                )
            ],
        )
        visual = {
            "opening_frame": f"Opening frame for {caption[:40]}",
            "visual_signature": ["creator", "close-up"],
            "confidence": 1.0,
        }
        return MultimodalResult(
            point_id,
            text,
            media,
            fused,
            {"uri": f"s3://test/{content_id}.mp4"},
            [{"offset_seconds": 0, "uri": f"s3://test/{content_id}.jpg"}],
            visual,
        )


class FakeTranscriptionProvider(TranscriptionProvider):
    def __init__(self, fixture_path: str | Path | None = None) -> None:
        self.fixture_path = Path(fixture_path) if fixture_path else None

    async def transcribe(self, shortcode: str, video_url: str | None) -> TranscriptResult:
        if not self.fixture_path or not self.fixture_path.exists():
            return TranscriptResult()
        rows = json.loads(self.fixture_path.read_text())
        row = next((item for item in rows if item["shortcode"] == shortcode), None)
        return (
            TranscriptResult(text=row.get("text", ""), language=row.get("language"))
            if row
            else TranscriptResult()
        )


class FixtureMetadataProvider(MetadataProvider):
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


class FixtureScraper(ScraperAdapter):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def scrape(
        self,
        keywords: list[str],
        limit: int,
        on_event: EmitFn = noop_emit,
        *,
        is_existing: Any = None,
    ) -> list[ScrapedItem]:
        _ = is_existing
        rows = json.loads(self.path.read_text())
        result: list[ScrapedItem] = []
        for keyword in keywords:
            await on_event(f"Searching fixture '{keyword}'.", step="keyword", keyword=keyword)
            matches = [row for row in rows if row.get("keyword", "").lower() == keyword.lower()]
            for row in matches[:limit]:
                canonical, shortcode = parse_instagram_url(row["url"])
                result.append(
                    ScrapedItem(
                        canonical_url=canonical,
                        shortcode=shortcode,
                        caption=row.get("caption", ""),
                        author=row.get("author"),
                        thumbnail_url=row.get("thumbnail_url"),
                        discovered_keyword=keyword,
                        metadata=row.get("metadata", {}),
                    )
                )
        return result


class FixtureInstagramProfileProvider(InstagramProfileProvider):
    def __init__(self, path: str | Path, redirect_uri: str = "http://test/callback") -> None:
        self.path = Path(path)
        self.redirect_uri = redirect_uri

    def authorization_url(self, state: str) -> str:
        return f"{self.redirect_uri}?{urlencode({'code': 'fixture-code', 'state': state})}"

    async def exchange_code(self, code: str) -> TokenBundle:
        if code != "fixture-code":
            raise InstagramGraphError("invalid fixture authorization code")
        return TokenBundle(
            "fixture-access-token",
            datetime.now(UTC) + timedelta(days=60),
            "fixture-instagram-user",
        )

    async def refresh_token(self, access_token: str) -> TokenBundle:
        if access_token != "fixture-access-token":
            raise InstagramNeedsReauth("fixture token is invalid")
        return TokenBundle(access_token, datetime.now(UTC) + timedelta(days=60))

    async def fetch_account(self, access_token: str) -> InstagramAccount:
        payload = self._payload().get("account", {})
        return InstagramAccount(
            str(payload.get("id", "fixture-instagram-user")),
            str(payload.get("username", "fixture_creator")),
            int(payload.get("follower_count", 0)),
        )

    async def fetch_recent_media(
        self, access_token: str, account_id: str, *, now: datetime
    ) -> list[InstagramMedia]:
        cutoff = now - timedelta(days=90)
        result: list[InstagramMedia] = []
        for index, row in enumerate(self._payload().get("media", [])):
            taken_at = datetime.fromisoformat(str(row["taken_at"]).replace("Z", "+00:00"))
            if taken_at < cutoff:
                continue
            permalink = row.get("permalink")
            shortcode = str(row.get("shortcode") or row.get("id") or f"fixture-{index}")
            if permalink:
                shortcode = [part for part in str(permalink).split("/") if part][-1]
            result.append(
                InstagramMedia(
                    id=str(row.get("id") or f"fixture-{index}"),
                    shortcode=shortcode,
                    caption=str(row.get("caption", "")),
                    media_type=str(row.get("media_type", "REELS")),
                    media_url=row.get("media_url"),
                    permalink=permalink,
                    taken_at=taken_at,
                    like_count=int(row.get("like_count", 0)),
                    comment_count=int(row.get("comment_count", 0)),
                    view_count=int(row.get("view_count", 0)),
                    share_count=int(row.get("share_count", 0)),
                    insights_available=bool(row.get("insights_available", True)),
                )
            )
        return sorted(result, key=lambda item: item.taken_at, reverse=True)[:15]

    async def fetch_audience(
        self, access_token: str, account_id: str, *, now: datetime
    ) -> InstagramAudience:
        return InstagramAudience(now, {}, {}, {}, {}, {}, (), ())

    def _payload(self) -> dict[str, Any]:
        value = json.loads(self.path.read_text())
        if not isinstance(value, dict):
            raise InstagramGraphError("fixture must be an object")
        return value


class FakeProfileSummaryProvider(ProfileSummaryProvider):
    async def summarize(self, context: ProfileSummaryContext) -> str:
        return (
            f"@{context.username}, {context.content_count} içerik ve "
            f"{context.average_viral_score:.1f} ortalama skor."
        )


class FakeRecommendationProvider(RecommendationProvider):
    name = "fake"

    async def generate(self, context: RecommendationContext) -> RecommendationProviderResult:
        formats = ("reels", "carousel", "native_photo")
        trends = context.trends or [TrendContext("güncel içerik", "", 0)]
        cards = []
        for index in range(context.count):
            trend = trends[(index + context.attempt) % len(trends)]
            sequence = context.attempt * context.count + index + 1
            topic = trend.title[:80] or "güncel içerik"
            cards.append(
                RecommendationCard(
                    title=f"{topic}: özgün seri #{sequence}",
                    hook=f"İlk 3 saniyede {topic.lower()} hakkındaki yanılgıyı göster.",
                    cta="Takipçilerine deneyimlerini sor.",
                    content_format=formats[(index + context.attempt) % len(formats)],
                    reasoning="Profil ve trend sinyalini özgün bir fikirde birleştirir.",
                )
            )
        return RecommendationProviderResult(
            cards, RecommendationUsage(), "deterministic-fake-v1"
        )


class FakeBrandAnalysisProvider(BrandAnalysisProvider):
    def __init__(self, posts: list[BrandAnalysisPost] | None = None) -> None:
        self.posts = posts or []

    async def resolve_username(self, username_or_url: str) -> str:
        return username_or_url.rstrip("/").split("/")[-1].removeprefix("@") or "testbrand"

    async def fetch_posts(
        self, username: str, max_posts: int, *, job_id: str
    ) -> list[BrandAnalysisPost]:
        if not self.posts:
            for index in range(min(max_posts, 3)):
                self.posts.append(
                    BrandAnalysisPost(
                        job_id=job_id,
                        post_id=f"post_{index}",
                        shortcode=f"shortcode_{index}",
                        permalink=f"https://www.instagram.com/p/shortcode_{index}/",
                        caption=f"Test post {index} for {username}",
                        media_type="IMAGE",
                        media_url=f"https://example.com/media_{index}.jpg",
                        fetched_at=datetime.now(UTC),
                    )
                )
        return self.posts[:max_posts]


class FakeBrandCaptionAnalyzer:
    async def analyze(self, caption: str) -> CaptionAnalysis:
        return CaptionAnalysis(
            tone="samimi" if len(caption) < 50 else "bilgilendirici",
            structure="hook-fayda-soru",
            hashtag_strategy="marka etiketleri",
            emoji_usage="orta",
            cta_type="soru",
            keywords=["test", "ürün"],
            target_audience_hint="25-45 yaş cilt bakımı",
            message_clarity_score=7,
        )


class FakeBrandAnalysisReportProvider:
    async def generate(self, context: BrandAnalysisReportContext) -> BrandAnalysisReport:
        from app.providers.brand_report import (
            _build_image_appendix,
            _fallback_brief_from_context,
            _render_brief_to_markdown,
        )

        brief = _fallback_brief_from_context(context)
        markdown = _render_brief_to_markdown(brief, context) + _build_image_appendix(context)
        return BrandAnalysisReport(
            schema_version="brand-analysis-report-v1",
            job_id=context.job_id,
            markdown_text=markdown,
            report_s3_key=f"reports/brand/{context.job_id}/report.md",
            strategic_brief=brief,
        )


class FakeBrandAnalysisPdfProvider(BrandAnalysisPdfProvider):
    def __init__(
        self,
        export_bytes: bytes = b"%PDF-1.4 generated",
        download_bytes: bytes = b"%PDF-1.4 cached",
    ) -> None:
        self.export_bytes = export_bytes
        self.download_bytes = download_bytes
        self.export_calls: list[tuple[str, str, str]] = []
        self.download_calls: list[str] = []

    async def export(
        self,
        job_id: str,
        markdown_text: str,
        target_username: str,
    ) -> BrandAnalysisPdf:
        self.export_calls.append((job_id, markdown_text, target_username))
        return BrandAnalysisPdf(
            job_id=job_id,
            pdf_bytes=self.export_bytes,
            pdf_s3_key=f"reports/brand/{job_id}/report.pdf",
        )

    async def download(self, s3_key: str) -> bytes:
        self.download_calls.append(s3_key)
        return self.download_bytes
