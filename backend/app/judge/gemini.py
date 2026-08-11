"""Shared Gemini (google-genai SDK) plumbing for the judge + narrative.

The SDK auto-detects Vertex from the environment; we force the **Developer API**
(``vertexai=False``) since auth is an API key. SDK errors are translated to our
retry/backoff error classes so the existing retry policy applies unchanged.

Audio is handed to the agents as an ``AudioRef`` — either a **Files API reference**
(uploaded once per job, referenced by every agent that needs it) or **inline bytes**
(the fallback). The Files API path avoids the Developer API's ~20 MB inline request
ceiling and stops re-sending the same audio in every agent call of a job.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.config import RateLimitSettings, Settings
from app.ratelimit.backoff import (
    FatalError,
    RateLimitError,
    RetryableError,
    retry_async,
)

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
    """Recording audio passed to the multimodal judge.

    Either a Files API reference (``file_uri`` set; preferred — uploaded once per job)
    or inline bytes (``data`` set; the fallback). ``file_name`` is the Files API
    resource name (``files/...``) used for post-job cleanup.
    """

    data: bytes | None
    mime_type: str
    file_uri: str | None = None
    file_name: str | None = None


def audio_part(audio: AudioRef) -> types.Part:
    """The request Part for an AudioRef — URI reference when available, else inline bytes.

    Single builder shared by every multimodal agent so the inline/Files-API decision
    lives in one place (the handler that constructs the AudioRef), not in each agent.
    """
    if audio.file_uri:
        return types.Part.from_uri(file_uri=audio.file_uri, mime_type=audio.mime_type)
    if audio.data is None:  # defensive: an AudioRef must carry one of the two
        raise FatalError("AudioRef has neither file_uri nor inline data")
    return types.Part.from_bytes(data=audio.data, mime_type=audio.mime_type)


def audio_mime_for(key: str) -> str:
    return _AUDIO_MIME.get(PurePosixPath(key).suffix.lower(), "audio/mpeg")


class AudioUploader(Protocol):
    """Uploads recording audio to a provider file store; returns a referenceable AudioRef."""

    async def upload(self, data: bytes, mime_type: str) -> AudioRef:
        ...

    async def delete(self, file_name: str) -> None:
        ...


class GeminiFileUploader:
    """Gemini Files API uploader (Developer API; free 20 GB store, 48 h auto-expiry).

    ``upload`` pushes the bytes once and polls until the file is ACTIVE, so the returned
    reference is immediately usable by ``generate_content``. Transient failures retry on
    the standard ladder; the *caller* decides the fallback (inline bytes) on final failure.
    ``delete`` is best-effort post-job hygiene — files self-expire after 48 h regardless.
    """

    def __init__(
        self,
        *,
        client: Any,
        retry: RateLimitSettings,
        poll_seconds: float = 2.0,
        timeout_seconds: float = 120.0,
        sleep_fn: Any = asyncio.sleep,
    ) -> None:
        self._client = client
        self._retry = retry
        self._poll = poll_seconds
        self._timeout = timeout_seconds
        self._sleep = sleep_fn

    @staticmethod
    def _state_name(file: Any) -> str:
        state = getattr(file, "state", None)
        return getattr(state, "name", str(state) if state is not None else "")

    async def _wait_active(self, name: str, first: Any) -> Any:
        """Poll files.get until ACTIVE (audio is usually ACTIVE immediately)."""
        file, waited = first, 0.0
        while self._state_name(file) == "PROCESSING":
            if waited >= self._timeout:
                raise RetryableError(
                    f"Files API file {name} still PROCESSING after {self._timeout:.0f}s"
                )
            await self._sleep(self._poll)
            waited += self._poll
            file = await self._client.aio.files.get(name=name)
        if self._state_name(file) != "ACTIVE":
            raise RetryableError(f"Files API file {name} in state {self._state_name(file)}")
        return file

    async def upload(self, data: bytes, mime_type: str) -> AudioRef:
        async def _do() -> AudioRef:
            try:
                file = await self._client.aio.files.upload(
                    file=io.BytesIO(data),
                    config=types.UploadFileConfig(mime_type=mime_type),
                )
                file = await self._wait_active(str(file.name), file)
            except (RetryableError, RateLimitError, FatalError):
                raise
            except Exception as exc:  # noqa: BLE001 — translated to retry taxonomy
                raise translate_genai_error(exc) from exc
            return AudioRef(
                data=None,
                mime_type=mime_type,
                file_uri=str(file.uri),
                file_name=str(file.name),
            )

        return await retry_async(
            _do,
            max_attempts=self._retry.retry_max_attempts,
            base=self._retry.retry_base_seconds,
            cap=self._retry.retry_cap_seconds,
            jitter_ratio=self._retry.retry_jitter_ratio,
        )

    async def delete(self, file_name: str) -> None:
        try:
            await self._client.aio.files.delete(name=file_name)
        except Exception:  # noqa: BLE001 — best-effort; files auto-expire in 48 h
            pass


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
