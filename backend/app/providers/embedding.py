"""Amazon Bedrock multimodal embedding provider."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import PurePosixPath
from typing import Any

import numpy as np

from app.core.config import Settings
from app.core.errors import TransientError, is_throttling_error


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(ABC):
    def __init__(self, vector_size: int) -> None:
        self.vector_size = vector_size

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    async def embed_media(self, s3_uri: str, *, purpose: str = "GENERIC_INDEX") -> list[float]:
        raise EmbeddingError("provider does not support media embeddings")


class BedrockEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.vector_size)
        self.settings = settings

    async def embed(self, text: str) -> list[float]:
        import asyncio

        return await asyncio.to_thread(self._embed_sync, text, None, "GENERIC_INDEX")

    async def embed_media(self, s3_uri: str, *, purpose: str = "GENERIC_INDEX") -> list[float]:
        import asyncio

        return await asyncio.to_thread(self._embed_sync, None, s3_uri, purpose)

    def _embed_sync(
        self, text: str | None, s3_uri: str | None, purpose: str
    ) -> list[float]:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

        client = boto3.client(
            "bedrock-runtime", region_name=self.settings.bedrock_embedding_region
        )
        if self.settings.bedrock_embedding_model_id.startswith("amazon.nova"):
            body = json.dumps(self._nova_request(text, s3_uri, purpose))
        else:
            if text is None:
                raise EmbeddingError("configured text model cannot embed media")
            body = json.dumps({"inputText": text, "dimensions": self.vector_size})
        try:
            response = client.invoke_model(
                modelId=self.settings.bedrock_embedding_model_id,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
        except ClientError as exc:
            if is_throttling_error(exc):
                raise TransientError(f"Bedrock embedding throttled: {exc}") from exc
            raise
        except BotoCoreError as exc:
            raise TransientError(f"Bedrock embedding transport error: {exc}") from exc
        payload = json.loads(response["body"].read())
        embedding = payload.get("embedding")
        if embedding is None:
            embedding = (payload.get("embeddings") or [{}])[0].get("embedding")
        if not isinstance(embedding, list):
            raise EmbeddingError("Bedrock response did not contain an embedding")
        if len(embedding) != self.vector_size:
            raise EmbeddingError(
                f"embedding dimension {len(embedding)} does not match configured "
                f"vector size {self.vector_size}"
            )
        return [float(value) for value in embedding]

    def _nova_request(
        self, text: str | None, s3_uri: str | None, purpose: str
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "embeddingPurpose": purpose,
            "embeddingDimension": self.vector_size,
        }
        if text is not None:
            params["text"] = {"truncationMode": "END", "value": text}
        elif s3_uri is not None:
            media_type, media_format = _media_type_and_format(s3_uri)
            location = {"uri": s3_uri}
            bucket = s3_uri.removeprefix("s3://").split("/", maxsplit=1)[0]
            owner = (
                self.settings.embedding_media_s3_bucket_owner
                if bucket == self.settings.embedding_media_s3_bucket
                else self.settings.media_s3_bucket_owner
            )
            if owner:
                location["bucketOwner"] = owner
            params[media_type] = {
                "format": media_format,
                "source": {"s3Location": location},
            }
            if media_type == "video":
                params[media_type]["embeddingMode"] = "AUDIO_VIDEO_COMBINED"
        else:
            raise EmbeddingError("embedding request requires text or S3 media")
        return {"taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": params}


class NoOpEmbeddingProvider(EmbeddingProvider):
    """Returns a deterministic, normalized random embedding without calling Bedrock.

    Useful for local development and tests where Bedrock is unavailable.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.vector_size)
        self.settings = settings

    async def embed(self, text: str) -> list[float]:
        return self._vector_for_seed(text)

    async def embed_media(self, s3_uri: str, *, purpose: str = "GENERIC_INDEX") -> list[float]:
        return self._vector_for_seed(f"{purpose}:{s3_uri}")

    def _vector_for_seed(self, seed: str) -> list[float]:
        digest = hashlib.sha256(seed.encode()).digest()
        generator = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        vector = generator.standard_normal(self.vector_size)
        norm = float(np.linalg.norm(vector))
        return [float(value) for value in (vector / norm if norm else vector)]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fake":
        return NoOpEmbeddingProvider(settings)
    if settings.embedding_provider == "aws":
        return BedrockEmbeddingProvider(settings)
    raise EmbeddingError(f"unknown embedding_provider: {settings.embedding_provider}")


_IMAGE_FORMATS = {
    ".gif": "gif",
    ".jpeg": "jpeg",
    ".jpg": "jpeg",
    ".png": "png",
    ".webp": "webp",
}
_VIDEO_FORMATS = {
    ".flv": "flv",
    ".mkv": "mkv",
    ".mov": "mov",
    ".mp4": "mp4",
    ".mpeg": "mpeg",
    ".mpg": "mpg",
    ".3gp": "three_gp",
    ".webm": "webm",
    ".wmv": "wmv",
}


def _media_type_and_format(s3_uri: str) -> tuple[str, str]:
    suffix = PurePosixPath(s3_uri.split("?", maxsplit=1)[0]).suffix.lower()
    if suffix in _IMAGE_FORMATS:
        return "image", _IMAGE_FORMATS[suffix]
    if suffix in _VIDEO_FORMATS:
        return "video", _VIDEO_FORMATS[suffix]
    raise EmbeddingError("unsupported S3 media extension for Nova embedding")
