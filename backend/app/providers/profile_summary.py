"""AI profile summary providers for Phase 5."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings


class ProfileSummaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfileSummaryContext:
    username: str
    follower_count: int
    content_count: int
    average_viral_score: float
    vector_std_dev: float
    content_samples: list[str]
    preferences: dict[str, list[str] | str] = field(default_factory=dict)
    structured_profile: dict[str, Any] = field(default_factory=dict)


class ProfileSummaryProvider(ABC):
    @abstractmethod
    async def summarize(self, context: ProfileSummaryContext) -> str:
        raise NotImplementedError


class BedrockProfileSummaryProvider(ProfileSummaryProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def summarize(self, context: ProfileSummaryContext) -> str:
        return await asyncio.to_thread(self._summarize_sync, context)

    def _summarize_sync(self, context: ProfileSummaryContext) -> str:
        import boto3

        client = boto3.client(
            "bedrock-runtime", region_name=self.settings.bedrock_generation_region
        )
        samples = "\n".join(f"- {item[:500]}" for item in context.content_samples[:10])
        prompt = (
            "Aşağıdaki Instagram profesyonel hesabını Türkçe, somut ve kullanıcıya dönük "
            "tek bir kısa paragrafta analiz et. Niş, ton, hedef kitle, güçlü yön ve gelişim "
            "fırsatını belirt. Yalnızca analiz metnini döndür.\n\n"
            f"Kullanıcı: @{context.username}\n"
            f"Takipçi: {context.follower_count}\n"
            f"İçerik sayısı: {context.content_count}\n"
            f"Ortalama viral skor: {context.average_viral_score:.2f}\n"
            f"Vektör çeşitlilik metriği: {context.vector_std_dev:.4f}\n"
            f"Hedef ve üretim tercihleri: {context.preferences}\n"
            f"Yapılandırılmış performans profili: {context.structured_profile}\n"
            f"İçerik örnekleri:\n{samples}"
        )
        response = client.converse(
            modelId=self.settings.bedrock_profile_model_id,
            system=[
                {
                    "text": (
                        "Sen sosyal medya stratejisti olarak yalnız verilen verilere dayalı, "
                        "abartısız bir içerik üreticisi profili çıkarırsın."
                    )
                }
            ],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={
                "maxTokens": self.settings.profile_summary_max_tokens,
                "temperature": 0.2,
            },
        )
        blocks: list[dict[str, Any]] = (
            response.get("output", {}).get("message", {}).get("content", [])
        )
        text = " ".join(str(block["text"]) for block in blocks if block.get("text")).strip()
        if not text:
            raise ProfileSummaryError("Bedrock did not return a profile summary")
        return text


def build_profile_summary_provider(settings: Settings) -> ProfileSummaryProvider:
    return BedrockProfileSummaryProvider(settings)
