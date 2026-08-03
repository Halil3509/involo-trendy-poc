"""Official read-only topic signal connectors; no page scraping."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.schemas.intelligence import TopicSignal


class TopicSignalProviderError(RuntimeError):
    pass


class TopicSignalProvider(ABC):
    source: str

    @abstractmethod
    async def fetch(self, topics: list[str]) -> list[TopicSignal]:
        raise NotImplementedError


class GoogleTrendsProvider(TopicSignalProvider):
    source = "google_trends"

    def __init__(self, settings: Settings) -> None:
        if not settings.google_trends_api_url or not settings.google_trends_api_key:
            raise TopicSignalProviderError(
                "approved Google Trends API URL and key are not configured"
            )
        self.url = settings.google_trends_api_url
        self.api_key = settings.google_trends_api_key.get_secret_value()

    async def fetch(self, topics: list[str]) -> list[TopicSignal]:
        result: list[TopicSignal] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for topic in topics:
                response = await client.get(
                    self.url,
                    params={"query": topic, "key": self.api_key},
                )
                if not response.is_success:
                    raise TopicSignalProviderError("Google Trends API request failed")
                payload = response.json()
                score = float(payload.get("interest", payload.get("score", 0)) or 0)
                result.append(
                    TopicSignal(
                        topic=topic,
                        source="google_trends",
                        license="Google APIs Terms of Service",
                        captured_at=utcnow(),
                        score=score,
                        volume=score,
                        source_url=f"https://trends.google.com/trends/explore?q={quote_plus(topic)}",
                        provenance={"api": "Google Trends API", "response_scope": "topic"},
                    )
                )
        return result


class YouTubeTopicSignalProvider(TopicSignalProvider):
    source = "youtube"
    API = "https://www.googleapis.com/youtube/v3"

    def __init__(self, settings: Settings) -> None:
        if not settings.youtube_api_key:
            raise TopicSignalProviderError("YouTube Data API key is not configured")
        self.api_key = settings.youtube_api_key.get_secret_value()

    async def fetch(self, topics: list[str]) -> list[TopicSignal]:
        result: list[TopicSignal] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for topic in topics:
                search = await client.get(
                    f"{self.API}/search",
                    params={
                        "part": "id",
                        "q": topic,
                        "type": "video",
                        "order": "date",
                        "maxResults": 10,
                        "key": self.api_key,
                    },
                )
                if not search.is_success:
                    raise TopicSignalProviderError("YouTube Data API search failed")
                ids = [
                    str(item.get("id", {}).get("videoId"))
                    for item in search.json().get("items", [])
                    if item.get("id", {}).get("videoId")
                ]
                views = 0
                if ids:
                    videos = await client.get(
                        f"{self.API}/videos",
                        params={
                            "part": "statistics",
                            "id": ",".join(ids),
                            "key": self.api_key,
                        },
                    )
                    if not videos.is_success:
                        raise TopicSignalProviderError("YouTube Data API statistics failed")
                    views = sum(
                        int(item.get("statistics", {}).get("viewCount", 0) or 0)
                        for item in videos.json().get("items", [])
                    )
                result.append(
                    TopicSignal(
                        topic=topic,
                        source="youtube",
                        license="YouTube API Services Terms of Service",
                        captured_at=utcnow(),
                        score=float(views),
                        volume=float(views),
                        source_url=(
                            "https://www.youtube.com/results?search_query=" + quote_plus(topic)
                        ),
                        provenance={
                            "api": "YouTube Data API v3",
                            "sample_size": len(ids),
                            "response_scope": "topic",
                        },
                    )
                )
        return result


class RedditTopicSignalProvider(TopicSignalProvider):
    source = "reddit"
    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    API = "https://oauth.reddit.com"

    def __init__(self, settings: Settings) -> None:
        if not settings.reddit_client_id or not settings.reddit_client_secret:
            raise TopicSignalProviderError("Reddit OAuth credentials are not configured")
        self.client_id = settings.reddit_client_id
        self.client_secret = settings.reddit_client_secret.get_secret_value()
        self.user_agent = settings.reddit_user_agent

    async def fetch(self, topics: list[str]) -> list[TopicSignal]:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": self.user_agent}) as client:
            token_response = await client.post(
                self.TOKEN_URL,
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
            )
            if not token_response.is_success:
                raise TopicSignalProviderError("Reddit OAuth token request failed")
            token = str(token_response.json()["access_token"])
            result: list[TopicSignal] = []
            for topic in topics:
                response = await client.get(
                    f"{self.API}/search",
                    params={"q": topic, "sort": "new", "limit": 25, "t": "week"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if not response.is_success:
                    raise TopicSignalProviderError("Reddit API search failed")
                children: list[dict[str, Any]] = (
                    response.json().get("data", {}).get("children", [])
                )
                engagement = sum(
                    int(item.get("data", {}).get("score", 0) or 0)
                    + int(item.get("data", {}).get("num_comments", 0) or 0)
                    for item in children
                )
                result.append(
                    TopicSignal(
                        topic=topic,
                        source="reddit",
                        license="Reddit Data API Terms",
                        captured_at=utcnow(),
                        score=float(engagement),
                        volume=float(len(children)),
                        source_url=f"https://www.reddit.com/search/?q={quote_plus(topic)}",
                        provenance={
                            "api": "Reddit OAuth Data API",
                            "sample_size": len(children),
                            "response_scope": "topic",
                        },
                    )
                )
        return result
