"""Objection embeddings (§7.5). Protocol + deterministic stub + Gemini embeddings.

Embeddings feed pgvector clustering for the portfolio "most common / never-cleared" view.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

import httpx

from app.config import RateLimitSettings
from app.models import EMBEDDING_DIM
from app.ratelimit.backoff import classify_http_status, retry_async


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class StubEmbedder:
    """Deterministic embeddings: identical text → identical vector (so identical objections
    cluster together in tests). Not semantic — for dev/tests only.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        # Each digest byte → a float in [0,1). Deterministic and always finite (no NaN/inf,
        # which pgvector rejects).
        out: list[float] = []
        seed = text.lower().strip()
        i = 0
        while len(out) < EMBEDDING_DIM:
            digest = hashlib.sha256(f"{seed}:{i}".encode()).digest()
            for byte in digest:
                out.append(byte / 256.0)
                if len(out) == EMBEDDING_DIM:
                    break
            i += 1
        return out


class GeminiEmbedder:
    """Gemini embeddings via REST (httpx, injectable + mockable)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        retry: RateLimitSettings,
        client: httpx.AsyncClient,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self._key = api_key
        self._model = model
        self._retry = retry
        self._client = client
        self._base = base_url.rstrip("/")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # gemini-embedding-* expose embedContent (single) + outputDimensionality. Request
        # EMBEDDING_DIM (768) to match the pgvector column. Objections per call are few, so
        # one request each is fine.
        vectors: list[list[float]] = []
        for text in texts:

            async def _do(t: str = text) -> dict[str, Any]:
                resp = await self._client.post(
                    f"{self._base}/models/{self._model}:embedContent",
                    params={"key": self._key},
                    json={
                        "content": {"parts": [{"text": t}]},
                        "outputDimensionality": EMBEDDING_DIM,
                    },
                )
                retry_after = resp.headers.get("retry-after")
                classify_http_status(resp.status_code, float(retry_after) if retry_after else None)
                return dict(resp.json())

            data = await retry_async(
                _do,
                max_attempts=self._retry.retry_max_attempts,
                base=self._retry.retry_base_seconds,
                cap=self._retry.retry_cap_seconds,
                jitter_ratio=self._retry.retry_jitter_ratio,
            )
            vectors.append(data["embedding"]["values"])
        return vectors
