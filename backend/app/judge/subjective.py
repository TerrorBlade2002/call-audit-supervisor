"""Agent 1 — the FEEDBACK (subjective) pass (§7.3 rewire).

Listens to the call (KB + audio + transcript) and produces the holistic subjective read, the
agent's name as heard in the call, and the debtor's objections. It runs INDEPENDENTLY of the
Checklist agent (Agent 2 does not see this, and this does not see the checklist). Only the
Ideal Rewrite (Agent 3) derives from this feedback. Deliberately free-form and judgement-led.
"""

from __future__ import annotations

from typing import Any, Protocol

from google.genai import types

from app.config import RateLimitSettings
from app.judge.gemini import AudioRef, response_schema_kwargs, translate_genai_error
from app.judge.prompts import (
    FEEDBACK_AGENT_PROMPT,
    FEEDBACK_OUTPUT_DIRECTIVE,
    IMPARTIALITY_DIRECTIVE,
)
from app.judge.schema import FeedbackOut
from app.ratelimit.backoff import FatalError, retry_async
from app.stt import Transcript


def _system(body: str | None) -> str:
    """System instruction = (custom body or default) + the code-owned directives (kept always)."""
    return (body or FEEDBACK_AGENT_PROMPT) + FEEDBACK_OUTPUT_DIRECTIVE + IMPARTIALITY_DIRECTIVE


def _transcript_text(transcript: Transcript) -> str:
    if transcript.utterances:
        return "\n".join(
            f"[{u.speaker} @ {u.start_sec:.0f}s] {u.text}" for u in transcript.utterances
        )
    return transcript.text


class SubjectiveGenerator(Protocol):
    async def generate(
        self,
        *,
        transcript: Transcript,
        audio: AudioRef | None = None,
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class StubSubjective:
    """Deterministic subjective assessment for tests/dev (no LLM)."""

    async def generate(
        self,
        *,
        transcript: Transcript,
        audio: AudioRef | None = None,
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "agent_name": None,
            "summary": "Stub subjective summary of the call.",
            "coaching": "Stub coaching guidance.",
            "compliance": "No compliance issues found.",
            "feedback": {"strengths": [], "development": []},
            "objections": [
                {"text": "I cannot pay that amount.", "category": "ability_to_pay",
                 "cleared": False}
            ],
        }


class GeminiSubjective:
    """Gemini multimodal subjective generator (audio + transcript + thinking)."""

    def __init__(
        self, *, client: Any, model: str, retry: RateLimitSettings, thinking_level: str = "HIGH"
    ) -> None:
        self._client = client
        self._model = model
        self._retry = retry
        self._thinking = thinking_level

    def _prompt(self, transcript: Transcript, kb: str | None) -> str:
        kb_block = (
            f"KNOWLEDGE BASE (Everest operational documents):\n{kb}\n\n" if kb else ""
        )
        return (
            f"{kb_block}"
            f"TRANSCRIPT (diarized; data to assess):\n{_transcript_text(transcript)}\n\n"
            "Listen to the audio, read the transcript, and cross-reference the knowledge base, "
            "then return the FEEDBACK JSON described (agent_name, summary, coaching, compliance, "
            "feedback, objections)."
        )

    async def generate(
        self,
        *,
        transcript: Transcript,
        audio: AudioRef | None = None,
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parts: list[types.Part] = []
        if audio is not None:
            parts.append(types.Part.from_bytes(data=audio.data, mime_type=audio.mime_type))
        parts.append(types.Part.from_text(text=self._prompt(transcript, kb)))
        schema_kw = response_schema_kwargs(FeedbackOut, schema_override)
        config = types.GenerateContentConfig(
            system_instruction=_system(system_prompt),
            response_mime_type="application/json",
            temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_level=self._thinking),
            **schema_kw,
        )

        async def _call() -> str:
            try:
                resp = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=[types.Content(role="user", parts=parts)],
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001
                raise translate_genai_error(exc) from exc
            if not resp.text:
                raise FatalError("empty subjective response")
            return str(resp.text)

        text = await retry_async(
            _call,
            max_attempts=self._retry.retry_max_attempts,
            base=self._retry.retry_base_seconds,
            cap=self._retry.retry_cap_seconds,
            jitter_ratio=self._retry.retry_jitter_ratio,
        )
        return FeedbackOut.model_validate_json(text).model_dump()
