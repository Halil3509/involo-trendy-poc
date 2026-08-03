"""Brand reference analysis service."""

from __future__ import annotations

import asyncio
import statistics
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.providers.brand_analysis import BrandAnalysisProvider
from app.providers.brand_caption import BrandCaptionAnalyzer
from app.providers.brand_report import BrandAnalysisReportProvider
from app.providers.media import Keyframe, MediaProvider
from app.providers.vision import VisionProvider
from app.schemas.brand_analysis import (
    BrandAnalysisPost,
    BrandAnalysisReportContext,
    BrandWorldSynthesis,
    CaptionAnalysis,
    ConfidenceLevel,
    ContentRecipe,
    ContentRecipeFormatRole,
    EvidenceChain,
    EvidenceReference,
    MediaEvidence,
    MetricObservation,
    PerformanceSummary,
    PostSummary,
)
from app.schemas.intelligence import VisualAnalysis
from app.services.provider_runs import record_provider_call


class BrandAnalysisService:
    def __init__(
        self,
        db: Any,
        settings: Settings,
        provider: BrandAnalysisProvider,
        media: MediaProvider,
        vision: VisionProvider,
        caption_analyzer: BrandCaptionAnalyzer,
        report_provider: BrandAnalysisReportProvider,
        *,
        is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.provider = provider
        self.media = media
        self.vision = vision
        self.caption_analyzer = caption_analyzer
        self.report_provider = report_provider
        self.is_cancelled = is_cancelled

    async def run(
        self,
        job_id: str,
        username_or_url: str,
        max_posts: int,
        *,
        emit: Any | None = None,
    ) -> dict[str, int]:
        """Fetch, analyze, and report on a target Instagram account's posts."""
        _emit: Any = emit if emit is not None else _noop_emit

        target_username = await self.provider.resolve_username(username_or_url)
        counters: dict[str, int] = {
            "resolved": 1,
            "fetched": 0,
            "analyzed": 0,
            "failed": 0,
            "requested": max_posts,
            "total": max_posts,
        }
        await _emit(
            f"Hedef hesap: @{target_username}",
            phase="resolving",
            current=1,
            total=max_posts,
            percent=5,
        )
        await self._update_job(
            job_id,
            {
                "target_username": target_username,
                "state": "fetching",
                "counters": counters,
            },
        )

        posts = await self.provider.fetch_posts(
            target_username,
            max_posts,
            job_id=job_id,
        )
        counters["total"] = len(posts)
        await _emit(
            f"{len(posts)} gönderi bulundu.",
            phase="fetching",
            current=0,
            total=counters["total"],
            percent=5,
        )

        for post in posts:
            try:
                await self._persist_post(post)
                counters["fetched"] += 1
            except Exception as exc:  # noqa: BLE001 - isolate individual post failures
                counters["failed"] += 1
                await _emit(f"Gönderi kaydedilemedi {post.post_id}: {exc}")
            else:
                await _emit(
                    f"Post {counters['fetched']}/{counters['total']} toplandı",
                    phase="fetching",
                    current=counters["fetched"],
                    total=counters["total"],
                    percent=self._percent(
                        counters["fetched"], counters["total"], 5, 50
                    ),
                )
            await self._update_job(job_id, {"counters": counters})

        await self._update_job(job_id, {"state": "fetched", "counters": counters})
        await _emit(
            f"{counters['fetched']} gönderi kaydedildi.",
            phase="fetched",
            current=counters["fetched"],
            total=counters["total"],
            percent=50,
        )

        await self._process_posts(job_id, posts, _emit, counters)
        if await self._cancelled():
            await _emit(
                "Analiz kullanıcı tarafından durduruldu.",
                phase="cancelled",
                current=counters["analyzed"],
                total=counters["total"],
                percent=self._percent(
                    counters["analyzed"], counters["total"], 50, 95
                ),
                terminal=True,
            )
            raise asyncio.CancelledError()
        await self._generate_report(job_id, target_username, _emit, counters)

        return counters

    async def _process_posts(
        self,
        job_id: str,
        posts: list[BrandAnalysisPost],
        emit: Any,
        counters: dict[str, int],
    ) -> None:
        total = counters["total"]
        await self._update_job(
            job_id, {"state": "analyzing", "counters": counters}
        )
        await emit(
            "Gönderiler AI ile analiz ediliyor...",
            phase="analyzing",
            current=0,
            total=total,
            percent=50,
        )
        semaphore = asyncio.Semaphore(self.settings.brand_analysis_concurrency)

        async def _analyze_one(post: BrandAnalysisPost) -> None:
            async with semaphore:
                if await self._cancelled():
                    return
                try:
                    await self._analyze_post(job_id, post)
                    counters["analyzed"] += 1
                    current = counters["analyzed"] + counters["failed"]
                    await emit(
                        f"Post {counters['analyzed']}/{total} analiz edildi",
                        phase="analyzing",
                        current=current,
                        total=total,
                        percent=self._percent(current, total, 50, 95),
                    )
                except Exception as exc:  # noqa: BLE001
                    counters["failed"] += 1
                    current = counters["analyzed"] + counters["failed"]
                    await emit(
                        f"Post analizi başarısız {post.post_id}: {exc}",
                        phase="analyzing",
                        current=current,
                        total=total,
                        percent=self._percent(current, total, 50, 95),
                    )
                await self._update_job(job_id, {"counters": counters})

        await asyncio.gather(*(_analyze_one(post) for post in posts))
        final_state = "cancelled" if await self._cancelled() else "reporting"
        await self._update_job(
            job_id, {"state": final_state, "counters": counters}
        )

    async def _analyze_post(self, job_id: str, post: BrandAnalysisPost) -> None:
        content_id = f"brand:{job_id}:{post.post_id}"
        if not post.media_url:
            raise BrandAnalysisServiceError(f"post {post.post_id} has no media URL")

        stored = await self.media.ingest(post.media_url, content_id)
        keyframes: list[Keyframe] = []
        if post.media_type in {"VIDEO", "REELS"}:
            keyframes = await self.media.extract_keyframes(
                stored, content_id, self.settings.brand_analysis_keyframe_offsets_seconds
            )
        elif post.media_type == "CAROUSEL_ALBUM" and post.media_items:
            for idx, item in enumerate(post.media_items[:6]):
                if not item.url or item.media_type not in {"IMAGE", "CAROUSEL_ALBUM"}:
                    continue
                try:
                    slide_stored = await self.media.ingest(
                        item.url, f"{content_id}:slide:{idx}"
                    )
                    keyframes.append(
                        Keyframe(media=slide_stored, offset_seconds=float(idx))
                    )
                except Exception:  # noqa: BLE001
                    pass

        async def _run_vision() -> VisualAnalysis:
            return await record_provider_call(
                self.db,
                provider="amazon_bedrock",
                model_id=self.settings.bedrock_vision_model_id,
                stage="brand_vision",
                operation=lambda: self.vision.analyze(stored, keyframes, caption=post.caption),
                subject_id=post.post_id,
                region=self.settings.bedrock_generation_region,
            )

        async def _run_caption() -> CaptionAnalysis:
            return await record_provider_call(
                self.db,
                provider="amazon_bedrock",
                model_id=self.settings.brand_analysis_caption_model_id,
                stage="brand_caption",
                operation=lambda: self.caption_analyzer.analyze(post.caption),
                subject_id=post.post_id,
                region=self.settings.bedrock_generation_region,
            )

        visual, caption_analysis = await asyncio.gather(_run_vision(), _run_caption())

        evidence_items: list[MediaEvidence] = list(post.media_items) if post.media_items else []
        if not evidence_items and post.media_url:
            evidence_items = [MediaEvidence(url=post.media_url, media_type=post.media_type)]

        if post.media_type in {"VIDEO", "REELS"} and keyframes:
            keyframe = keyframes[0]
            keyframe_url = self.media.public_url(keyframe.media)
            for item in evidence_items:
                if item.media_type in {"VIDEO", "REELS"}:
                    item.url = keyframe_url
                    item.media_type = "IMAGE"
                    item.label = item.label or f"{post.shortcode} screenshot"
                    item.offset_seconds = keyframe.offset_seconds

        post.media_items = evidence_items

        update = {
            "media_s3_key": stored.key,
            "visual_analysis": visual.model_dump(),
            "caption_analysis": caption_analysis.model_dump(),
            "analyzed_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "media_items": [item.model_dump() for item in evidence_items],
        }
        await self.db.brand_analysis_posts.update_one(
            {"job_id": job_id, "post_id": post.post_id},
            {"$set": update},
        )

    async def _generate_report(
        self,
        job_id: str,
        target_username: str,
        emit: Any,
        counters: dict[str, int],
    ) -> None:
        total = counters["total"]
        if await self._cancelled():
            await emit(
                "Analiz kullanıcı tarafından durduruldu.",
                phase="cancelled",
                current=counters["analyzed"],
                total=total,
                percent=95,
                terminal=True,
            )
            raise asyncio.CancelledError()
        cursor = self.db.brand_analysis_posts.find({"job_id": job_id})
        analyzed = await cursor.sort("fetched_at", -1).to_list(
            length=self.settings.brand_analysis_max_report_posts
        )
        if not analyzed:
            await emit(
                "Analiz edilecek gönderi bulunamadı, rapor oluşturulamıyor.",
                phase="reporting",
                current=0,
                total=total,
                percent=95,
            )
            return

        await emit(
            "Marka raporu hazırlanıyor...",
            phase="reporting",
            current=counters["analyzed"],
            total=total,
            percent=95,
        )
        context = self._build_report_context(job_id, target_username, analyzed)
        report = await record_provider_call(
            self.db,
            provider="amazon_bedrock",
            model_id=self.settings.brand_analysis_report_model_id,
            stage="brand_report",
            operation=lambda: self.report_provider.generate(context),
            subject_id=job_id,
            region=self.settings.bedrock_generation_region,
        )

        await self.db.brand_analysis_reports.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "job_id": job_id,
                    "markdown_text": report.markdown_text,
                    "report_s3_key": report.report_s3_key,
                    "strategic_brief": (
                        report.strategic_brief.model_dump()
                        if report.strategic_brief
                        else None
                    ),
                    "schema_version": report.schema_version,
                    "updated_at": datetime.now(UTC),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )
        await self._update_job(
            job_id,
            {
                "report_s3_key": report.report_s3_key,
                "report_text": report.markdown_text[:2000],
                "state": "analyzed",
                "counters": counters,
            },
        )
        await emit(
            "Rapor hazırlandı.",
            phase="completed",
            current=total,
            total=total,
            percent=100,
        )

    _SEMANTIC_TAG_MAP: dict[str, list[str]] = {
        "nature": ["doğal", "bitki", "çiçek", "yeşil", "farm", "organik", "botanical", "nature"],
        "science": ["bilim", "araştırma", "klinik", "etkin", "sonuç", "teknoloji", "formula"],
        "ritual": ["ritüel", "rutin", "adım", "süreç", "uygula", "uygulama", "routine"],
        "founder_authority": ["tata", "kurucu", "ben", "biz", "founder", "harper", "kendi"],
        "provenance": ["vermont", "çiftlik", "farm", "yerel", "source", "origin", "köken"],
        "sustainability": ["sürdürülebilir", "geri dönüşüm", "doğa dostu", "eco", "sustainable"],
        "sensory": ["hisset", "dokun", "kokusu", "doku", "texture", "scent", "feel", "sensorial"],
        "luxury": ["lüks", "premium", "kalite", "işçilik", "luxury", "elegant", "sophisticated"],
        "community": ["siz", "topluluk", "birlikte", "community", "together", "join"],
        "aspiration": ["hayal", "yaşam tarzı", "lifestyle", "aspirational", "dream", "glow"],
    }

    # Domain inference used to avoid forcing skincare/cosmetic language onto SaaS,
    # education, travel, fashion, or other non-beauty accounts.
    _DOMAIN_KEYWORDS: dict[str, list[str]] = {
        "saas_tech": [
            "yapay zeka", "ai", "yazılım", "software", "app", "uygulama", "platform",
            "dashboard", "panel", "otomasyon", "automation", "üretkenlik", "productivity",
            "içerik stratejisi", "sosyal medya", "büyüme", "growth", "analytics", "veri",
            "api", "code", "kod", "tool", "saas", "b2b", "creator", "influencer",
            "marka yönetimi", "instagram", "linkedin", "post", "schedule", "plan",
            "takvim", "yönetim", "metrik",
        ],
        "education": [
            "öğren", "eğitim", "ders", "course", "tutorial", "ipucu", "tips", "nasıl",
            "how to", "rehber", "guide", "adım", "step", "dönüşüm", "learn", "bilgi",
        ],
        "physical_beauty": [
            "cilt", "skin", "krem", "cream", "serum", "makyaj", "makeup", "bakım", "care",
            "doku", "texture", "swatch", "spf", "nem", "hydration", "glow", "akne",
            "yaşlanma", "güzellik", "beauty", "skincare", "cosmetic", "kozmetik", "ürün",
            "before", "after", "packshot", "bottle", "jar", "tube",
        ],
        "fashion": [
            "outfit", "kombin", "giyim", "moda", "fashion", "stil", "style", "look",
            "aksesuar", "çanta", "ayakkabı", "shoe", "dress", "elbise", "trend", "tarz",
        ],
        "food": [
            "yemek", "tarif", "recipe", "lezzet", "food", "mutfak", "kitchen", "chef",
            "restoran", "restaurant", "yiyecek", "içecek", "drink", "tatlı", "pasta",
            "kahve", "coffee",
        ],
        "fitness_lifestyle": [
            "fitness", "spor", "egzersiz", "workout", "sağlık", "health", "wellness",
            "yoga", "pilates", "gym", "antrenman", "travel", "seyahat", "otel", "hotel",
            "macera", "adventure",
        ],
    }

    _DOMAIN_TAG_ALLOWLISTS: dict[str, set[str]] = {
        "saas_tech": {"science", "community", "founder_authority", "luxury", "aspiration"},
        "education": {
            "science", "community", "founder_authority", "luxury", "aspiration", "ritual"
        },
        "physical_beauty": set(_SEMANTIC_TAG_MAP),
        "fashion": {
            "nature", "sensory", "luxury", "community", "aspiration", "sustainability", "ritual",
            "provenance",
        },
        "food": set(_SEMANTIC_TAG_MAP),
        "fitness_lifestyle": {
            "nature", "community", "aspiration", "ritual", "sustainability", "sensory",
            "provenance",
        },
        "unknown": set(_SEMANTIC_TAG_MAP),
    }

    _CONTENT_JOB_VOCABULARY: set[str] = {
        "educate_with_lifestyle_context",
        "demonstrate_efficacy_through_proof",
        "build_trust_with_community_voice",
        "convert_from_desire",
        "participate_in_brand_ritual",
        "sell_aspirational_lifestyle",
        "create_desire_through_tension",
        "create_desire_through_texture_and_proof",
        "present_key_message",
    }

    _PREMIUM_SIGNAL_VOCABULARY: set[str] = {
        "language_of_craft",
        "provenance",
        "sensory_detail",
        "materiality",
        "authority",
        "community",
        "education",
        "entertainment",
        "premium",
        "proof",
        "urgency",
    }

    _EMOTIONAL_EFFECT_MAP: dict[str, str] = {
        "nature": "doğallık",
        "science": "güven veren etkinlik",
        "ritual": "rutinel bağlılık",
        "founder_authority": "samimi inandırıcılık",
        "provenance": "şeffaf köken",
        "sustainability": "bilinçli seçim",
        "sensory": "duyusal kanıt",
        "luxury": "seçkinlik",
        "community": "aidiyet",
        "aspiration": "yaşam tarzı özlemi",
    }

    _PHYSICAL_DOMAINS: set[str] = {"physical_beauty", "fashion", "food", "fitness_lifestyle"}

    _ANOMALY_KEYWORDS: dict[str, list[str]] = {
        "giveaway": ["çekiliş", "giveaway", "hediye", "kazan", "kazanan", "katıl", "çekilis"],
        "collab": ["iş birliği", "collab", "partner", "birlikte", "takas", "sponsor"],
        "launch": ["launch", "yeni", "çıkıyor", "tanıtım", "yenilik", "new product"],
        "event": ["etkinlik", "event", "canlı", "live", "festival", "günler"],
    }

    def _detect_anomaly(
        self, caption: str, caption_analysis: dict[str, Any], visual_analysis: dict[str, Any]
    ) -> str | None:
        text = caption.lower()
        for anomaly_type, keywords in self._ANOMALY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return anomaly_type
        caption_anomaly = caption_analysis.get("anomaly_candidate") if caption_analysis else None
        if caption_anomaly:
            return str(caption_anomaly)
        return None

    def _extract_semantic_tags(
        self,
        caption: str,
        visual_analysis: dict[str, Any],
        domain: str = "",
    ) -> list[str]:
        text = caption.lower()
        tags: set[str] = set()
        for tag, keywords in self._SEMANTIC_TAG_MAP.items():
            if any(kw in text for kw in keywords):
                tags.add(tag)
        if visual_analysis:
            visual_text_parts: list[str] = []
            visual_text_parts.extend(
                str(item) for item in visual_analysis.get("visual_signature", [])
            )
            visual_text_parts.append(str(visual_analysis.get("contextual_placement", "")))
            visual_text_parts.append(
                str(visual_analysis.get("aspirational_lifestyle_narrative", ""))
            )
            visual_text_parts.append(str(visual_analysis.get("visual_hook", "")))
            visual_text_parts.append(str(visual_analysis.get("material_context", "")))
            visual_text_parts.extend(
                str(item) for item in visual_analysis.get("sensory_visual_proof", [])
            )
            visual_text = " ".join(visual_text_parts).lower()
            for tag, keywords in self._SEMANTIC_TAG_MAP.items():
                if any(kw in visual_text for kw in keywords):
                    tags.add(tag)
            for obj in visual_analysis.get("objects", []):
                obj_lower = str(obj).lower()
                if any(kw in obj_lower for kw in self._SEMANTIC_TAG_MAP["nature"]):
                    tags.add("nature")
                if any(kw in obj_lower for kw in self._SEMANTIC_TAG_MAP["luxury"]):
                    tags.add("luxury")
        allowed = self._DOMAIN_TAG_ALLOWLISTS.get(domain)
        if allowed is not None:
            tags = {tag for tag in tags if tag in allowed}
        return sorted(tags)

    def _extract_content_job(
        self,
        caption: str,
        caption_analysis: dict[str, Any],
        domain: str = "",
    ) -> str:
        text = caption.lower()
        cta = str(caption_analysis.get("cta_type", "")).lower() if caption_analysis else ""
        aspiration = str(caption_analysis.get("aspiration_level", "")).lower()
        arc = caption_analysis.get("narrative_arc", [])
        first_arc = str(arc[0]).lower() if isinstance(arc, list) and arc else ""

        llm_job = (
            str(caption_analysis.get("content_job", "")).strip()
            if caption_analysis
            else ""
        )
        if llm_job and llm_job in self._CONTENT_JOB_VOCABULARY:
            return llm_job

        if any(kw in text for kw in ["öğren", "nasıl", "bilgi", "keşfet", "neden"]):
            return "educate_with_lifestyle_context"
        if any(kw in text for kw in ["sonuç", "before", "after", "fark", "etki", "değişim"]):
            return "demonstrate_efficacy_through_proof"
        if any(
            kw in text
            for kw in ["güven", "yorum", "memnun", "review", "sizden", "gerçek"]
        ):
            return "build_trust_with_community_voice"
        if any(
            kw in text
            for kw in ["istiyorum", "satın", "shop", "link", "sipariş", "kodu"]
        ):
            return "convert_from_desire"
        if "giveaway" in cta or "çekiliş" in cta:
            return "participate_in_brand_ritual"
        if "lifestyle" in aspiration or "yaşam tarzı" in text or "ritüel" in text:
            return "sell_aspirational_lifestyle"
        if first_arc in {"problem_setup", "tension", "myth_bust"}:
            return "create_desire_through_tension"
        if (
            domain in self._PHYSICAL_DOMAINS
            and any(kw in text for kw in ["doku", "texture", "dokun", "kokusu", "hisset", "scent"])
        ):
            return "create_desire_through_texture_and_proof"
        return "present_key_message"

    def _extract_premium_signals(
        self,
        caption: str,
        caption_analysis: dict[str, Any],
        visual_analysis: dict[str, Any],
        domain: str = "",
    ) -> list[str]:
        signals: set[str] = set()
        text = caption.lower()
        physical = domain in self._PHYSICAL_DOMAINS

        if any(kw in text for kw in ["lüks", "luxury", "premium", "kalite"]):
            if physical:
                signals.add("language_of_craft")
            else:
                signals.add("premium")
        if physical and any(
            kw in text
            for kw in ["vermont", "çiftlik", "farm", "source", "origin", "köken", "yerel"]
        ):
            signals.add("provenance")
        if physical and any(
            kw in text for kw in ["doku", "kokusu", "hisset", "texture", "scent"]
        ):
            signals.add("sensory_detail")
        if physical and visual_analysis and any(
            kw in str(item).lower()
            for item in visual_analysis.get("visual_signature", [])
            for kw in ["green", "gold", "glass", "minimal"]
        ):
            signals.add("materiality")
        analysis_signals = (
            caption_analysis.get("premium_signals", []) if caption_analysis else []
        )
        if isinstance(analysis_signals, list):
            for signal in analysis_signals:
                if isinstance(signal, str):
                    normalized = signal.strip().lower()
                    if normalized in self._PREMIUM_SIGNAL_VOCABULARY:
                        signals.add(normalized)
                    else:
                        underscored = normalized.replace(" ", "_")
                        if underscored in self._PREMIUM_SIGNAL_VOCABULARY:
                            signals.add(underscored)
        return sorted(signals)

    def _infer_domain(
        self,
        caption: str,
        visual_analysis: dict[str, Any],
        visual_summary: str = "",
    ) -> str:
        text = f"{caption} {visual_summary}".lower()
        if visual_analysis:
            for field in (
                "contextual_placement",
                "aspirational_lifestyle_narrative",
                "visual_hook",
                "material_context",
                "aesthetic_style",
                "composition_style",
                "lighting_type",
            ):
                text += " " + str(visual_analysis.get(field, "")).lower()
            text += " " + " ".join(
                str(item).lower() for item in visual_analysis.get("objects", [])
            )
            text += " " + " ".join(
                str(item).lower() for item in visual_analysis.get("visual_signature", [])
            )
        scores: dict[str, int] = {}
        for candidate, keywords in self._DOMAIN_KEYWORDS.items():
            scores[candidate] = sum(1 for kw in keywords if kw in text)
        if not any(scores.values()):
            if visual_analysis and any(
                kw in text
                for kw in ["bottle", "jar", "tube", "cream", "serum", "skin", "texture", "makeup"]
            ):
                return "physical_beauty"
            return "unknown"
        return max(scores.items(), key=lambda kv: kv[1])[0]

    def _dominant_domain(self, posts: list[PostSummary]) -> str:
        counts: dict[str, int] = {}
        for post in posts:
            if post.domain:
                counts[post.domain] = counts.get(post.domain, 0) + 1
        if not counts:
            return "unknown"
        return max(counts.items(), key=lambda kv: kv[1])[0]

    def _build_evidence_reference(
        self, post: PostSummary, field: str, excerpt: str, why_supports: str
    ) -> EvidenceReference:
        return EvidenceReference(
            shortcode=post.shortcode,
            permalink=post.permalink,
            field=field,
            excerpt=excerpt[:300],
            why_supports=why_supports[:300],
            confidence=post.confidence,
        )

    def _build_content_recipe(
        self,
        organic_posts: list[PostSummary],
        format_breakdown: dict[str, int],
        taken_at_values: list[datetime],
    ) -> ContentRecipe:
        post_count = len(organic_posts)
        total = sum(format_breakdown.values()) or 1
        formats: list[ContentRecipeFormatRole] = []
        for fmt, count in sorted(format_breakdown.items(), key=lambda x: -x[1]):
            role = "sergileme" if fmt in {"IMAGE", "CAROUSEL_ALBUM"} else "anlatım"
            jobs: list[str] = []
            for post in organic_posts:
                if post.media_type == fmt and post.content_job:
                    jobs.append(post.content_job)
            top_jobs = sorted(
                {job: jobs.count(job) for job in jobs}.items(),
                key=lambda x: -x[1],
            )[:3]
            formats.append(
                ContentRecipeFormatRole(
                    format=fmt,
                    count=count,
                    percentage=round(count / total * 100, 1),
                    role_in_brand_world=role,
                    content_jobs=[job for job, _ in top_jobs],
                    confidence="medium" if count > 1 else "low",
                )
            )

        if len(taken_at_values) >= 2:
            sorted_dates = sorted(taken_at_values)
            window_days = max((sorted_dates[-1] - sorted_dates[0]).days, 1)
            gaps = [
                (sorted_dates[i] - sorted_dates[i - 1]).days
                for i in range(1, len(sorted_dates))
            ]
            avg_gap = sum(gaps) / len(gaps)
            posts_per_week = round(7 / avg_gap, 1) if avg_gap > 0 else None
            cadence_estimate = f"Ortalama {round(avg_gap, 1)} günde bir paylaşım"
            cadence_confidence: ConfidenceLevel = "medium" if window_days >= 14 else "low"
        else:
            window_days = None
            posts_per_week = None
            cadence_estimate = "Yetersiz tarih aralığı"
            cadence_confidence = "low"

        content_job_pairs: list[list[str]] = []
        for job in sorted({post.content_job for post in organic_posts if post.content_job}):
            content_job_pairs.append(
                [job, "Marka dünyasına hizmet eden tekrar eden içerik görevi"]
            )

        return ContentRecipe(
            observed_window_days=window_days,
            coverage_label="Son gönderilerden gözlenen örneklem",
            cadence_estimate=cadence_estimate,
            posts_per_week_estimate=posts_per_week,
            cadence_confidence=cadence_confidence,
            formats=formats,
            content_jobs=content_job_pairs,
            anomaly_count=0,
            anomaly_note=(
                "Organik gönderiler üzerinden hesaplandı; "
                "çekiliş ve kampanyalar ayrılmıştır."
            ),
            confidence="medium" if post_count >= 3 else "low",
        )

    def _build_performance_summary(
        self,
        all_posts: list[PostSummary],
        organic_posts: list[PostSummary],
        anomaly_posts: list[PostSummary],
        total_views: int,
    ) -> PerformanceSummary:
        data_quality_notes: list[str] = []
        organic_metrics: list[MetricObservation] = []
        anomaly_metrics: list[MetricObservation] = []
        valid_rate_comparisons: list[str] = []
        invalid_rate_comparisons: list[str] = []

        if total_views:
            data_quality_notes.append(
                "Görüntülenme verisi mevcut; etkileşim oranları view bazlı hesaplanabilir."
            )
        else:
            data_quality_notes.append(
                "Görüntülenme verisi mevcut değil; toplam etkileşim değerleri "
                "karşılaştırılabilir oran değildir."
            )
            invalid_rate_comparisons.append(
                "Görüntülenme verisi olmadan formatlar arası etkileşim oranı kıyaslanamaz."
            )

        for group, metrics_list, label in [
            (organic_posts, organic_metrics, "organik"),
            (anomaly_posts, anomaly_metrics, "anomalik"),
        ]:
            if not group:
                continue
            total_likes = sum(p.like_count for p in group)
            total_comments = sum(p.comment_count for p in group)
            total_shares = sum(p.share_count for p in group)
            total_views_g = sum(p.view_count for p in group)
            metrics_list.extend(
                [
                    MetricObservation(
                        label=f"{label} toplam beğeni",
                        value=float(total_likes),
                        basis="raw_total",
                        comparable=False,
                        confidence="medium",
                    ),
                    MetricObservation(
                        label=f"{label} toplam yorum",
                        value=float(total_comments),
                        basis="raw_total",
                        comparable=False,
                        confidence="medium",
                    ),
                ]
            )
            if total_views_g:
                rate = round((total_likes + total_comments + total_shares) / total_views_g, 4)
                metrics_list.append(
                    MetricObservation(
                        label=f"{label} view başına etkileşim",
                        value=rate,
                        basis="interactions_per_view",
                        comparable=True,
                        confidence="medium" if len(group) > 1 else "low",
                    )
                )
                if label == "organik":
                    valid_rate_comparisons.append(
                        "Aynı view-denominator’lu gönderiler kendi aralarında kıyaslanabilir."
                    )

        if not total_views and organic_posts:
            raw_values = [p.like_count + p.comment_count + p.share_count for p in organic_posts]
            metrics_list = organic_metrics
            metrics_list.append(
                MetricObservation(
                    label="Organik gönderilerde medyan ham etkileşim",
                    value=float(statistics.median(raw_values) if raw_values else 0.0),
                    basis="median_raw",
                    comparable=False,
                    confidence="low",
                    note="View verisi olmadan bu değer oran değildir.",
                )
            )

        return PerformanceSummary(
            organic_metrics=organic_metrics,
            anomaly_metrics=anomaly_metrics,
            data_quality_notes=data_quality_notes,
            valid_rate_comparisons=valid_rate_comparisons,
            invalid_rate_comparisons=invalid_rate_comparisons,
        )

    def _build_evidence_chains(
        self,
        organic_posts: list[PostSummary],
        domain: str = "",
    ) -> list[EvidenceChain]:
        if not domain:
            domain = self._dominant_domain(organic_posts)
        tag_to_posts: dict[str, list[PostSummary]] = {}
        for post in organic_posts:
            for tag in post.semantic_tags:
                tag_to_posts.setdefault(tag, []).append(post)

        tag_meanings: dict[str, tuple[str, str, str, str]] = {
            "nature": (
                "Doğal/organik unsurlar tekrar ediyor.",
                "Marka doğallık ve şeffaflık dünyası kuruyor.",
                "Doğal dil hedef kitleye saflık ve güven hissi verir.",
                (
                    "Kendi içeriklerinizde benzersiz doğal köken, yerel kaynak "
                    "veya şeffaf süreçleri öne çıkarın."
                ),
            ),
            "science": (
                "Sonuç, veri veya uzmanlık dili kullanılıyor.",
                "Etkinlik vaadi güvenilirlik ve net beklenti yaratıyor.",
                "Kanıt odaklı iletişim hedef kitleye güven verir.",
                "Sunduğunuz değerin kanıtlanabilir etkinliğini görsel ve metinle birleştirin.",
            ),
            "ritual": (
                "Rutin/adım/süreç anlatımı var.",
                "Kullanım bir alışkanlık veya deneyim ritüeline dönüşüyor.",
                "Tekrar edilebilir senaryolar bağlılığı ve tekrar kullanımı artırır.",
                "İzleyiciye uygulanabilir bir adım veya rutin sunun.",
            ),
            "founder_authority": (
                "Kurucu/ekip sesi ve kişisel hikaye öne çıkıyor.",
                "Marka kişisel ve otoriter bir ses kuruyor.",
                "İnsan hikâyesi ve samimiyet güveni artırır.",
                "Sahip/ekip hikayesini içeriklerinize yerleştirin.",
            ),
            "provenance": (
                "Köken/üretim/süreç yeri vurgulanıyor.",
                "Köken şeffaflığı güven ve premium algıyı destekliyor.",
                "Hedef kitle kaynağı ve süreci bilmek ister.",
                "Tedarik, üretim veya süreç kökeninizi görselleştirin.",
            ),
            "sustainability": (
                "Sürdürülebilirlik/eco mesajları var.",
                "Çevre bilinci marka değerlerini güçlendiriyor.",
                "Sürdürülebilirlik değer uyumu sağlar.",
                "Ambalaj ve süreçlerdeki sürdürülebilir adımları paylaşın.",
            ),
            "sensory": (
                "Duyusal detaylar (doku, malzeme, his) vurgulanıyor.",
                "Duyusal dil vaadi somut ve arzulanabilir kılıyor.",
                "Hissettirilebilir deneyim vaat inandırıcılığını artırır.",
                "Doku, malzeme, renk veya uygulama anlarını yakından gösterin.",
            ),
            "luxury": (
                "Premium/lüks dil ve görseller var.",
                "Premium kodlar markayı seçkin ve kaliteli konumlandırıyor.",
                "Premium algı fiyat ve kalite beklentisini yükseltir.",
                "Kalite, detay, malzeme ve ışık seçimlerinde sadelik ve özeni öne çıkarın.",
            ),
            "community": (
                "Topluluk/kullanıcı odaklı mesajlar var.",
                "Topluluk dili katılım ve aidiyet yaratıyor.",
                "Kullanıcı sesi güveni ve erişimi artırır.",
                "Kullanıcı içeriği ve yorumlarını marka kanalında sergileyin.",
            ),
            "aspiration": (
                "Yaşam tarzı/başarı dünyası kuruluyor.",
                "Marka sunduğu değeri bir yaşam tarzı veya hedef durumun parçası olarak sunuyor.",
                "Aspirasyonel içerik takipçinin kendini görmesini sağlar.",
                "Hedef kitlenizin arzuladığı senaryoyu somut içeriklere dönüştürün.",
            ),
        }

        chains: list[EvidenceChain] = []
        for tag, posts in sorted(tag_to_posts.items(), key=lambda x: -len(x[1])):
            if len(posts) < 2:
                continue
            meaning = tag_meanings.get(tag)
            if not meaning:
                continue
            observation, semantic_meaning, preference_hypothesis, principle = meaning
            evidence = [
                self._build_evidence_reference(
                    post,
                    "caption/visual",
                    f"caption={post.caption[:120]}...; visual={post.visual_summary[:160]}",
                    (
                        f"'{tag}' kodu; caption_analysis={post.caption_summary[:120]}"
                        if post.caption_summary
                        else f"'{tag}' kodu; visual: {post.visual_summary[:120]}"
                    ),
                )
                for post in posts[:3]
            ]
            chains.append(
                EvidenceChain(
                    chain_id=f"chain-{tag}",
                    observation=observation,
                    semantic_meaning=semantic_meaning,
                    preference_hypothesis=preference_hypothesis,
                    adaptable_principle=principle,
                    strategic_decision=f"{principle}; ilk test için 3-5 içerik üretin.",
                    evidence=evidence,
                    alternative_explanation=(
                        "Tekrar başka bir sebeple de olabilir; "
                        "a/b testi ile doğrulayın."
                    ),
                    confidence="medium" if len(posts) >= 3 else "low",
                )
            )
        return chains[:5]

    def _build_brand_world(
        self,
        organic_posts: list[PostSummary],
        domain: str = "",
    ) -> BrandWorldSynthesis:
        if not organic_posts:
            return BrandWorldSynthesis(
                emotional_effect="Yeterli organik gönderi yok.",
                confidence="low",
            )
        if not domain:
            domain = self._dominant_domain(organic_posts)
        visual_codes: list[str] = []
        verbal_codes: list[str] = []
        tag_counts: dict[str, int] = {}
        lifestyle_fragments: list[str] = []
        placement_fragments: list[str] = []
        proof_fragments: list[str] = []
        for post in organic_posts:
            for tag in post.semantic_tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            if post.premium_signals:
                visual_codes.extend(post.premium_signals)
            if post.content_job:
                verbal_codes.append(post.content_job)
            if post.visual_summary:
                if "lifestyle=" in post.visual_summary:
                    lifestyle_fragments.append(post.visual_summary.split("lifestyle=")[1].split(";")[0].strip()[:200])
                if "scene=" in post.visual_summary:
                    placement_fragments.append(post.visual_summary.split("scene=")[1].split(";")[0].strip()[:200])
                if "proof=" in post.visual_summary:
                    proof_fragments.append(post.visual_summary.split("proof=")[1].split(";")[0].strip()[:200])

        top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:5]
        top_tag_names = [tag for tag, _ in top_tags]

        emotional_effect = "; ".join(
            self._EMOTIONAL_EFFECT_MAP.get(tag, tag) for tag in top_tag_names
        ) or "gözlenen veriye dayalı marka hissi belirsiz"

        lifestyle_context = " ".join(list(dict.fromkeys(lifestyle_fragments))[:3])
        premium_mechanism = ""
        if placement_fragments or proof_fragments:
            proof_label = (
                "Materyal kanıtı" if domain in self._PHYSICAL_DOMAINS else "Görsel kanıt"
            )
            premium_mechanism = (
                f"{proof_label}: {', '.join(list(dict.fromkeys(proof_fragments))[:3])}. "
                f"Sahne konumlandırma: {'; '.join(list(dict.fromkeys(placement_fragments))[:2])}."
            ).strip()

        return BrandWorldSynthesis(
            emotional_effect=emotional_effect,
            brand_promise="",
            persona="",
            visual_codes=sorted(set(visual_codes))[:10],
            verbal_codes=sorted({v for v in verbal_codes if v})[:10],
            lifestyle_context=lifestyle_context,
            premium_mechanism=premium_mechanism,
            avoided_elements=[],
            confidence="medium" if len(organic_posts) >= 3 else "low",
            domain=domain,
        )

    def _build_report_context(
        self,
        job_id: str,
        target_username: str,
        analyzed: list[dict[str, Any]],
    ) -> BrandAnalysisReportContext:
        posts: list[PostSummary] = []
        total_likes = total_comments = total_views = total_shares = 0
        format_breakdown: dict[str, int] = {}
        caption_lengths: list[int] = []
        cta_values: list[str] = []
        tone_values: list[str] = []
        hashtag_values: list[str] = []
        emoji_values: list[str] = []
        visual_values: list[str] = []
        image_narratives: list[str] = []
        lifestyle_narratives: list[str] = []
        sensory_proofs: list[str] = []
        contextual_placements: list[str] = []
        taken_at_values: list[datetime] = []
        data_quality_notes: list[str] = []

        for doc in analyzed:
            like_count = int(doc.get("like_count", 0) or 0)
            comment_count = int(doc.get("comment_count", 0) or 0)
            view_count = int(doc.get("view_count", 0) or 0)
            share_count = int(doc.get("share_count", 0) or 0)
            total_likes += like_count
            total_comments += comment_count
            total_views += view_count
            total_shares += share_count
            media_type = str(doc.get("media_type", "MEDIA"))
            format_breakdown[media_type] = format_breakdown.get(media_type, 0) + 1
            caption = str(doc.get("caption", ""))
            caption_lengths.append(len(caption))

            caption_analysis = doc.get("caption_analysis") or {}
            if not isinstance(caption_analysis, dict):
                caption_analysis = {}
            visual_analysis = doc.get("visual_analysis") or {}
            if not isinstance(visual_analysis, dict):
                visual_analysis = {}

            cta = str(caption_analysis.get("cta_type", ""))
            if cta:
                cta_values.append(cta)
            tone = str(caption_analysis.get("tone", ""))
            if tone:
                tone_values.append(tone)
            hashtag = str(caption_analysis.get("hashtag_strategy", ""))
            if hashtag:
                hashtag_values.append(hashtag)
            emoji = str(caption_analysis.get("emoji_usage", ""))
            if emoji:
                emoji_values.append(emoji)
            signature = visual_analysis.get("visual_signature", [])
            if signature:
                visual_values.extend(str(item) for item in signature[:5])

            contextual = visual_analysis.get("contextual_placement")
            if contextual:
                contextual_placements.append(str(contextual))
            lifestyle_narr = visual_analysis.get("aspirational_lifestyle_narrative")
            if lifestyle_narr:
                lifestyle_narratives.append(str(lifestyle_narr))
                image_narratives.append(str(lifestyle_narr))
            visual_hook = visual_analysis.get("visual_hook")
            material = visual_analysis.get("material_context")
            if visual_hook:
                image_narratives.append(str(visual_hook))
            if material:
                image_narratives.append(str(material))
            sensory_proof = visual_analysis.get("sensory_visual_proof", [])
            if isinstance(sensory_proof, list):
                sensory_proofs.extend(str(item) for item in sensory_proof)

            caption_summary = self._summarize_caption(caption_analysis)
            visual_summary = self._summarize_visual(visual_analysis)
            media_items = doc.get("media_items") or []
            domain = self._infer_domain(caption, visual_analysis, visual_summary)

            if view_count:
                engagement_rate = round(
                    (like_count + comment_count + share_count) / view_count, 4
                )
                engagement_basis: Any = "interactions_per_view"
                engagement_comparable = True
                confidence: Any = "high"
            else:
                engagement_rate = None
                engagement_basis = "interactions_total_proxy"
                engagement_comparable = False
                confidence = "medium"

            semantic_tags = self._extract_semantic_tags(
                caption, visual_analysis, domain
            )
            content_job = self._extract_content_job(
                caption, caption_analysis, domain
            )
            premium_signals = self._extract_premium_signals(
                caption, caption_analysis, visual_analysis, domain
            )
            anomaly = self._detect_anomaly(caption, caption_analysis, visual_analysis)

            posts.append(
                PostSummary(
                    shortcode=str(doc.get("shortcode", "")),
                    media_type=media_type,
                    permalink=doc.get("permalink"),
                    caption=caption[:4000],
                    media_items=media_items[:10],
                    like_count=like_count,
                    comment_count=comment_count,
                    view_count=view_count,
                    share_count=share_count,
                    taken_at=doc.get("taken_at"),
                    caption_summary=caption_summary,
                    visual_summary=visual_summary,
                    engagement_rate=engagement_rate,
                    engagement_basis=engagement_basis,
                    engagement_comparable=engagement_comparable,
                    semantic_tags=semantic_tags,
                    content_job=content_job,
                    anomaly_candidate=anomaly,
                    premium_signals=premium_signals,
                    confidence=confidence,
                    domain=domain,
                )
            )

            taken_at = doc.get("taken_at")
            if isinstance(taken_at, datetime):
                taken_at_values.append(taken_at)
            elif isinstance(taken_at, str):
                try:
                    parsed = datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
                    taken_at_values.append(parsed)
                except ValueError:
                    pass

        post_count = len(posts)
        organic_posts = [p for p in posts if not p.anomaly_candidate]
        anomaly_posts = [p for p in posts if p.anomaly_candidate]
        dominant_domain = self._dominant_domain(organic_posts)

        if not total_views:
            data_quality_notes.append(
                "Görüntülenme verisi mevcut olmadığı için etkileşim oranı hesaplanamadı; "
                "toplam etkileşim değerleri verildi."
            )
        if not total_shares:
            data_quality_notes.append(
                "Paylaşım verisi Instagram Business Discovery yanıtında mevcut değil."
            )
        if any(not post.caption for post in posts):
            data_quality_notes.append(
                "Bazı gönderiler caption içermiyor; caption örüntüleri eksik örneklemle çıkarıldı."
            )
        if anomaly_posts:
            data_quality_notes.append(
                f"{len(anomaly_posts)} gönderi çekiliş/kampanya/olağandışı sinyali içeriyor; "
                "organik performanstan ayrıldı."
            )

        sorted_by_engagement = sorted(
            ((p.shortcode, p.engagement_rate) for p in posts if p.engagement_rate is not None),
            key=lambda item: item[1] or 0.0,
            reverse=True,
        )
        top_post_shortcode = sorted_by_engagement[0][0] if sorted_by_engagement else None
        bottom_post_shortcode = sorted_by_engagement[-1][0] if sorted_by_engagement else None

        def _frequencies(values: list[str]) -> dict[str, int]:
            result: dict[str, int] = {}
            for value in values:
                result[value] = result.get(value, 0) + 1
            return dict(sorted(result.items(), key=lambda item: (-item[1], item[0]))[:10])

        def _distribution(values: list[float]) -> dict[str, float]:
            if not values:
                return {}
            sorted_values = sorted(values)
            if len(sorted_values) == 1:
                q25 = q75 = sorted_values[0]
            else:
                quants = statistics.quantiles(sorted_values, n=4)
                q25 = quants[0]
                q75 = quants[2]
            return {
                "min": sorted_values[0],
                "p25": q25,
                "median": statistics.median(sorted_values),
                "p75": q75,
                "max": sorted_values[-1],
                "std": statistics.stdev(sorted_values) if len(sorted_values) > 1 else 0.0,
            }

        engagement_rates = [p.engagement_rate for p in posts if p.engagement_rate is not None]
        avg_engagement_rate = (
            round(sum(engagement_rates) / len(engagement_rates), 4)
            if engagement_rates
            else None
        )
        distribution_values = [
            p.engagement_rate
            if p.engagement_rate is not None
            else float(p.like_count + p.comment_count + p.share_count)
            for p in posts
        ]

        def _posting_rhythm_summary() -> str:
            if len(taken_at_values) < 2:
                return "Yetersiz gönderi tarihi; yayın ritmi çıkarılamadı."
            sorted_dates = sorted(taken_at_values)
            gaps = [
                (sorted_dates[i] - sorted_dates[i - 1]).days
                for i in range(1, len(sorted_dates))
            ]
            avg_gap = round(sum(gaps) / len(gaps), 1)
            window_days = (sorted_dates[-1] - sorted_dates[0]).days
            coverage = "güçlü" if window_days >= 14 else "sınırlı"
            return (
                f"Ortalama {avg_gap} günde bir paylaşım; "
                f"gözlem aralığı {window_days} gün ({coverage} kapsam)."
            )

        content_recipe = self._build_content_recipe(
            organic_posts, format_breakdown, taken_at_values
        )
        performance_summary = self._build_performance_summary(
            posts, organic_posts, anomaly_posts, total_views
        )
        semantic_observations = self._build_evidence_chains(
            organic_posts, dominant_domain
        )
        if not semantic_observations:
            semantic_observations = [
                EvidenceChain(
                    chain_id="chain-generic",
                    observation=(
                        "Gözlenen örneklemde tekrar eden semantik motif belirgin değil."
                    ),
                    semantic_meaning=(
                        "Marka dünyası hakkında güçlü bir çıkarım için "
                        "yeterli kanıt yok."
                    ),
                    preference_hypothesis=(
                        "Müşteri markasının beğenme nedeni belirsiz; "
                        "hipotez test edilmeli."
                    ),
                    adaptable_principle=(
                        "Daha geniş ve çeşitli örneklem toplandıktan sonra "
                        "semantik motifleri yeniden değerlendirin."
                    ),
                    strategic_decision=(
                        "Sonraki analizde en az 9-12 gönderilik örneklem ve "
                        "format çeşitliliği hedefleyin."
                    ),
                    alternative_explanation=(
                        "Mevcut örneklem küçük veya homojen olabilir."
                    ),
                    confidence="low",
                )
            ]
        brand_world = self._build_brand_world(organic_posts, dominant_domain)

        return BrandAnalysisReportContext(
            job_id=job_id,
            target_username=target_username,
            post_count=post_count,
            total_likes=total_likes,
            total_comments=total_comments,
            total_views=total_views,
            total_shares=total_shares,
            avg_likes=total_likes / post_count if post_count else 0.0,
            avg_comments=total_comments / post_count if post_count else 0.0,
            avg_views=total_views / post_count if post_count else 0.0,
            avg_shares=total_shares / post_count if post_count else 0.0,
            avg_engagement_rate=avg_engagement_rate,
            engagement_basis="interactions_per_view" if total_views else "interactions_total_proxy",
            data_quality_notes=data_quality_notes,
            format_breakdown=format_breakdown,
            caption_patterns={
                "average_length": (
                    round(sum(caption_lengths) / len(caption_lengths), 1)
                    if caption_lengths
                    else 0
                ),
                "cta_frequency": _frequencies(cta_values),
            },
            visual_patterns={"signature_frequency": _frequencies(visual_values)},
            top_post_shortcode=top_post_shortcode,
            bottom_post_shortcode=bottom_post_shortcode,
            posts=sorted(posts, key=lambda p: p.engagement_rate or 0.0, reverse=True),
            posting_rhythm_summary=_posting_rhythm_summary(),
            format_performance={
                fmt: {
                    "count": count,
                    "note": "View verisi varsa view-başı etkileşim kıyaslanabilir.",
                }
                for fmt, count in format_breakdown.items()
            },
            engagement_distribution=_distribution(distribution_values),
            tone_frequency=_frequencies(tone_values),
            cta_frequency=_frequencies(cta_values),
            hashtag_frequency=_frequencies(hashtag_values),
            emoji_frequency=_frequencies(emoji_values),
            visual_signature_frequency=_frequencies(visual_values),
            semantic_observations=semantic_observations,
            content_recipe=content_recipe,
            performance_summary=performance_summary,
            brand_world=brand_world,
            image_narratives=image_narratives[:30],
            lifestyle_narratives=lifestyle_narratives[:30],
            sensory_proof_frequency=_frequencies(sensory_proofs),
            contextual_placement_frequency=_frequencies(contextual_placements),
            anomaly_post_shortcodes=[p.shortcode for p in anomaly_posts],
        )

    @staticmethod
    def _summarize_caption(caption_analysis: Any) -> str:
        if not caption_analysis:
            return ""
        if isinstance(caption_analysis, CaptionAnalysis):
            parts = [
                f"ton={caption_analysis.tone}",
                f"yapı={caption_analysis.structure}",
                f"cta={caption_analysis.cta_type}",
            ]
            if caption_analysis.hook_type:
                parts.append(f"hook={caption_analysis.hook_type}")
            if caption_analysis.narrative_arc:
                parts.append(f"arc={caption_analysis.narrative_arc}")
            if caption_analysis.persona_triggers:
                parts.append(f"triggers={caption_analysis.persona_triggers}")
            if caption_analysis.aspiration_level:
                parts.append(f"aspiration={caption_analysis.aspiration_level}")
            return "; ".join(parts)
        if isinstance(caption_analysis, dict):
            parts = [
                f"ton={caption_analysis.get('tone', '')}",
                f"yapı={caption_analysis.get('structure', '')}",
                f"cta={caption_analysis.get('cta_type', '')}",
            ]
            hook = caption_analysis.get("hook_type", "")
            if hook:
                parts.append(f"hook={hook}")
            arc = caption_analysis.get("narrative_arc", [])
            if arc:
                parts.append(f"arc={arc}")
            triggers = caption_analysis.get("persona_triggers", [])
            if triggers:
                parts.append(f"triggers={triggers}")
            aspiration = caption_analysis.get("aspiration_level", "")
            if aspiration:
                parts.append(f"aspiration={aspiration}")
            return "; ".join(parts)
        return ""

    @staticmethod
    def _summarize_visual(visual_analysis: Any) -> str:
        if not visual_analysis:
            return ""
        if isinstance(visual_analysis, dict):
            parts: list[str] = []
            contextual = visual_analysis.get("contextual_placement")
            if contextual:
                parts.append(f"scene={contextual}")
            lifestyle = visual_analysis.get("aspirational_lifestyle_narrative")
            if lifestyle:
                parts.append(f"lifestyle={lifestyle}")
            proof = visual_analysis.get("sensory_visual_proof")
            if proof:
                parts.append(f"proof={proof}")
            hook = visual_analysis.get("visual_hook")
            if hook:
                parts.append(f"hook={hook}")
            material = visual_analysis.get("material_context")
            if material:
                parts.append(f"material={material}")
            signature = visual_analysis.get("visual_signature")
            if signature:
                parts.append(f"signature={signature}")
            color_palette = visual_analysis.get("color_palette")
            if color_palette:
                parts.append(f"colors={color_palette}")
            lighting = visual_analysis.get("lighting_type")
            if lighting:
                parts.append(f"lighting={lighting}")
            aesthetic = visual_analysis.get("aesthetic_style")
            if aesthetic:
                parts.append(f"aesthetic={aesthetic}")
            textures = visual_analysis.get("texture_descriptors")
            if textures:
                parts.append(f"textures={textures}")
            angle = visual_analysis.get("shooting_angle")
            if angle:
                parts.append(f"angle={angle}")
            return "; ".join(parts)
        return ""

    async def _persist_post(self, post: Any) -> None:
        document = post.model_dump() if hasattr(post, "model_dump") else dict(post)
        document["updated_at"] = datetime.now(UTC)
        await self.db.brand_analysis_posts.update_one(
            {"job_id": document["job_id"], "post_id": document["post_id"]},
            {
                "$set": document,
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )

    async def _update_job(self, job_id: str, fields: dict[str, Any]) -> None:
        await self.db.job_runs.update_one(
            {"task_id": job_id},
            {"$set": {**fields, "updated_at": utcnow()}},
            upsert=True,
        )

    @staticmethod
    def _percent(current: int, total: int, start: int, end: int) -> int:
        if total <= 0:
            return start
        return start + (current * (end - start)) // total

    async def _cancelled(self) -> bool:
        if self.is_cancelled is None:
            return False
        try:
            return await self.is_cancelled()
        except Exception:  # noqa: BLE001
            return False


class BrandAnalysisServiceError(RuntimeError):
    pass


async def _noop_emit(message: str, **kwargs: Any) -> None:
    pass
