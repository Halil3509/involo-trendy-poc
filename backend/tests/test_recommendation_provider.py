import sys
from types import SimpleNamespace
from typing import Any

import pytest
from provider_doubles import FakeRecommendationProvider

from app.core.config import Settings
from app.providers.recommendations import (
    BedrockRecommendationProvider,
    RecommendationContext,
    RecommendationProviderError,
    TrendContext,
)


def context(count: int = 3) -> RecommendationContext:
    return RecommendationContext(
        profile_summary="Sürdürülebilir moda odaklanan samimi bir üretici.",
        trends=[TrendContext("Dolap dönüşümü", "Öncesi ve sonrası", 84.0)],
        past_ideas=[],
        count=count,
    )


@pytest.mark.asyncio
async def test_fake_provider_is_deterministic_and_structured() -> None:
    provider = FakeRecommendationProvider()

    first = await provider.generate(context())
    second = await provider.generate(context())

    assert first.recommendations == second.recommendations
    assert len(first.recommendations) == 3
    assert {card.content_format for card in first.recommendations} == {
        "reels",
        "carousel",
        "native_photo",
    }


def test_bedrock_provider_uses_message_cache_point_and_forced_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Client:
        def converse(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            cards = [
                {
                    "title": f"Fikir {index}",
                    "hook": "Merak uyandıran giriş",
                    "cta": "Yorumunu paylaş",
                    "content_format": "reels",
                    "reasoning": "Kullanıcının nişine ve trende uygundur.",
                }
                for index in range(3)
            ]
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "name": "submit_content_recommendations",
                                    "input": {"recommendations": cards},
                                }
                            }
                        ]
                    }
                },
                "usage": {
                    "inputTokens": 100,
                    "outputTokens": 50,
                    "cacheReadInputTokens": 20,
                    "cacheWriteInputTokens": 10,
                },
            }

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda *_args, **_kwargs: Client()),
    )
    provider = BedrockRecommendationProvider(
        Settings(recommendation_prompt_cache=True)
    )

    result = provider._generate_sync(context())

    assert len(result.recommendations) == 3
    assert {"cachePoint": {"type": "default"}} in captured["messages"][0]["content"]
    assert "cachePoint" not in captured["toolConfig"]["tools"][0]
    assert captured["toolConfig"]["toolChoice"]["tool"]["name"] == (
        "submit_content_recommendations"
    )
    assert result.usage.cache_read_input_tokens == 20


def test_bedrock_provider_rejects_invalid_tool_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def converse(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "name": "submit_content_recommendations",
                                    "input": {"recommendations": [{"title": "Eksik"}]},
                                }
                            }
                        ]
                    }
                }
            }

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda *_args, **_kwargs: Client()),
    )

    with pytest.raises(RecommendationProviderError):
        BedrockRecommendationProvider(Settings())._generate_sync(context())
