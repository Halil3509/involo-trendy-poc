"""Caption analysis provider for brand reference posts."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.brand_analysis import CaptionAnalysis


class BrandCaptionAnalyzerError(RuntimeError):
    pass


class BrandCaptionAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, caption: str) -> CaptionAnalysis:
        raise NotImplementedError


class BedrockBrandCaptionAnalyzer(BrandCaptionAnalyzer):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any | None = None

    async def analyze(self, caption: str) -> CaptionAnalysis:
        return await asyncio.to_thread(self._analyze_sync, caption)

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "bedrock-runtime", region_name=self.settings.bedrock_generation_region
            )
        return self._client

    def _additional_model_request_fields(self) -> dict[str, Any] | None:
        if not self.settings.bedrock_enable_prompt_cache:
            return None
        return {"promptCache": {"enabled": True}}

    def _analyze_sync(self, caption: str) -> CaptionAnalysis:

        schema = json.dumps(
            CaptionAnalysis.model_json_schema(),
            separators=(",", ":"),
            ensure_ascii=False,
        )
        prompt = (
            "Analyze the following Instagram caption and return only a single valid JSON object "
            "matching the schema below. Do not use markdown code fences, explanations, or any text "
            "outside the JSON object.\n\n"
            "ADDITIONAL FIELD INSTRUCTIONS:\n"
            "- hook_type: Classify the opening hook style "
            "(e.g. 'curiosity_gap', 'bold_claim', 'problem_statement', 'question', 'story').\n"
            "- narrative_arc: For carousels, list the per-slide narrative roles "
            "(e.g. ['hook', 'educate', 'proof', 'cta']). For single posts, use ['single'].\n"
            "- persona_triggers: List psychological triggers the caption activates "
            "(e.g. 'bilimsel_kanit', 'dogal_icerik', 'rituel', 'aidelik').\n"
            "- aspiration_level: Classify the aspiration tier "
            "('premium', 'accessible', 'mass').\n"
            "- slide_count_estimate: If this is a carousel, estimate the number of slides "
            "from caption structure; otherwise 0.\n\n"
            f"Schema:\n{schema}\n\n"
            f"<caption>{caption[:5000]}</caption>"
        )
        response = self._get_client().converse(
            modelId=self.settings.brand_analysis_caption_model_id,
            system=[
                {
                    "text": (
                        "Sen sosyal medya caption'larını marka stratejisi açısından analiz eden "
                        "bir uzman asistanısın. Yalnızca verilen şemaya uygun JSON döndür."
                    )
                }
            ],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={
                "maxTokens": self.settings.brand_analysis_caption_max_tokens,
                "temperature": 0.2,
            },
            additionalModelRequestFields=self._additional_model_request_fields(),
        )
        blocks: list[dict[str, Any]] = (
            response.get("output", {}).get("message", {}).get("content", [])
        )
        raw_text = "".join(block.get("text", "") for block in blocks if "text" in block)
        payload = _extract_json_payload(raw_text)
        if payload is None:
            raise BrandCaptionAnalyzerError(
                f"Caption analysis response contained no valid JSON: {raw_text[:500]!r}"
            )
        try:
            return CaptionAnalysis.model_validate(payload)
        except ValidationError as exc:
            raise BrandCaptionAnalyzerError(
                f"Caption analysis response did not match schema: {exc.errors()}"
            ) from exc


class FakeBrandCaptionAnalyzer(BrandCaptionAnalyzer):
    async def analyze(self, caption: str) -> CaptionAnalysis:
        return CaptionAnalysis(
            tone="bilgilendirici" if len(caption) > 50 else "samimi",
            structure="hook-fayda-soru",
            hashtag_strategy="orta-düzey marka etiketleri",
            emoji_usage="minimal",
            cta_type="soru",
            keywords=["ürün", "cilt", "bakım"],
            target_audience_hint="cilt bakımıyla ilgilenen 25-45 yaş",
            message_clarity_score=7,
            hook_type="curiosity_gap" if len(caption) > 50 else "question",
            narrative_arc=["hook", "educate", "cta"],
            persona_triggers=["doğal içerik", "ritüel"],
            aspiration_level="accessible",
            slide_count_estimate=3,
        )


def build_brand_caption_analyzer(settings: Settings) -> BrandCaptionAnalyzer:
    if settings.brand_analysis_provider == "fake":
        return FakeBrandCaptionAnalyzer()
    if settings.brand_analysis_provider == "aws":
        return BedrockBrandCaptionAnalyzer(settings)
    raise BrandCaptionAnalyzerError(
        f"unknown brand_analysis_provider: {settings.brand_analysis_provider}"
    )


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    text = _strip_markdown_code_block(text.strip())
    start = text.find("{")
    if start == -1:
        return None
    closes = [i for i, ch in enumerate(text) if ch == "}"]
    for end in reversed(closes):
        if end < start:
            break
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _strip_markdown_code_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text[3:].lstrip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        text = text.rstrip()
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text
