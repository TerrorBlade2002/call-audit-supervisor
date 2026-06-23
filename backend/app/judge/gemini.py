"""Shared Gemini (google-genai SDK) plumbing for the judge + narrative.

The SDK auto-detects Vertex from the environment; we force the **Developer API**
(``vertexai=False``) since auth is an API key. SDK errors are translated to our
retry/backoff error classes so the existing retry policy applies unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from pydantic import BaseModel

from app.config import Settings
from app.ratelimit.backoff import FatalError, RateLimitError, RetryableError

_AUDIO_MIME = {
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".opus": "audio/opus",
    ".webm": "audio/webm",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
}


@dataclass(frozen=True)
class AudioRef:
    """Recording audio passed to the multimodal judge (inline bytes)."""

    data: bytes
    mime_type: str


def audio_mime_for(key: str) -> str:
    return _AUDIO_MIME.get(PurePosixPath(key).suffix.lower(), "audio/mpeg")


def build_gemini_client(settings: Settings) -> genai.Client:
    """Developer-API client (not Vertex). Constructed once per process."""
    return genai.Client(api_key=settings.gemini_api_key, vertexai=False)


def strip_developer_unsupported_schema_keywords(schema: Any) -> Any:
    """Return a copy of a JSON schema without Gemini Developer API-incompatible keywords."""
    if isinstance(schema, dict):
        return {
            key: strip_developer_unsupported_schema_keywords(value)
            for key, value in schema.items()
            if key not in {"additionalProperties", "additional_properties"}
        }
    if isinstance(schema, list):
        return [strip_developer_unsupported_schema_keywords(value) for value in schema]
    return schema


def response_schema_kwargs(
    default_schema: type[BaseModel],
    schema_override: dict[str, Any] | None,
) -> dict[str, Any]:
    if schema_override is not None:
        return {
            "response_json_schema": strip_developer_unsupported_schema_keywords(schema_override)
        }
    return {"response_schema": default_schema}


def translate_genai_error(exc: Exception) -> Exception:
    """Map google-genai SDK errors to our retryable/fatal taxonomy."""
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        if code == 429:
            return RateLimitError(str(exc))
        if isinstance(code, int) and 500 <= code < 600:
            return RetryableError(str(exc))
        return FatalError(str(exc))
    return exc
