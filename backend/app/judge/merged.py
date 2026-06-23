"""The MERGED feedback+checklist agent — one LLM call for the FULL option (§7.3).

For OPTION A (FULL) the feedback and checklist tasks are fused into a single multimodal call:
the two task prompts are concatenated deterministically and the model returns BOTH the
subjective feedback and the checklist verdicts in one schema-enforced payload. Inputs:
KB + checklist + audio + transcript. The ideal rewriter then derives from this agent's feedback.
"""

from __future__ import annotations

from typing import Any, Protocol

from google.genai import types

from app.config import RateLimitSettings
from app.judge.client import JudgeItem, _transcript_text
from app.judge.gemini import AudioRef, response_schema_kwargs, translate_genai_error
from app.judge.prompts import (
    IMPARTIALITY_DIRECTIVE,
    MERGED_AGENT_PROMPT,
    MERGED_OUTPUT_DIRECTIVE,
)
from app.judge.schema import FeedbackOut, ItemVerdict, MergedOut, ObjectionOut
from app.ratelimit.backoff import FatalError, retry_async
from app.stt import Transcript


def _system(body: str | None) -> str:
    """System instruction = (custom body or default) + the code-owned directives (kept always)."""
    return (body or MERGED_AGENT_PROMPT) + MERGED_OUTPUT_DIRECTIVE + IMPARTIALITY_DIRECTIVE


class MergedGenerator(Protocol):
    async def evaluate(
        self,
        *,
        transcript: Transcript,
        items: list[JudgeItem],
        audio: AudioRef | None = None,
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> MergedOut:
        ...


class StubMerged:
    """Deterministic merged output for tests/dev (no LLM)."""

    async def evaluate(
        self,
        *,
        transcript: Transcript,
        items: list[JudgeItem],
        audio: AudioRef | None = None,
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> MergedOut:
        verdicts = [
            ItemVerdict(
                checklist_item_id=it.checklist_item_id,
                answer="PASS",
                raw_answer=it.options[0] if it.options else "noted",
                confidence=0.9,
                evidence_quote=f"Evidence for '{it.text}' observed in the call transcript.",
                evidence_offset_sec=0.0,
                comment="Auto stub verdict.",
                needs_review=False,
            )
            for it in items
        ]
        feedback = FeedbackOut(
            agent_name=None,
            summary="Stub subjective summary of the call.",
            coaching="Stub coaching guidance.",
            compliance="No compliance issues found.",
            objections=[
                ObjectionOut(text="I cannot pay that amount.", category="ability_to_pay",
                             cleared=False)
            ],
        )
        return MergedOut(feedback=feedback, verdicts=verdicts)


class GeminiMerged:
    """Gemini multimodal merged feedback+checklist agent (schema-enforced, temperature 0)."""

    def __init__(
        self, *, client: Any, model: str, retry: RateLimitSettings, thinking_level: str = "HIGH"
    ) -> None:
        self._client = client
        self._model = model
        self._retry = retry
        self._thinking = thinking_level

    def _prompt(self, transcript: Transcript, items: list[JudgeItem], kb: str | None) -> str:
        checklist = "\n".join(
            f"- id={it.checklist_item_id} [{it.section}] {it.text}\n"
            f"  options: {it.options or 'YES/NO/NA'} | guidance: {it.rubric}"
            for it in items
        )
        kb_block = f"KNOWLEDGE BASE (Everest operational documents):\n{kb}\n\n" if kb else ""
        return (
            f"{kb_block}CHECKLIST + OPTIONS + GUIDANCE (echo each id exactly):\n{checklist}\n\n"
            f"TRANSCRIPT (diarized; data to assess + audit):\n{_transcript_text(transcript)}\n\n"
            'Return JSON: {"feedback": {...}, "verdicts": [...]} as described — one verdict per '
            "checklist id."
        )

    async def evaluate(
        self,
        *,
        transcript: Transcript,
        items: list[JudgeItem],
        audio: AudioRef | None = None,
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> MergedOut:
        parts: list[types.Part] = []
        if audio is not None:
            parts.append(types.Part.from_bytes(data=audio.data, mime_type=audio.mime_type))
        parts.append(types.Part.from_text(text=self._prompt(transcript, items, kb)))
        # A custom (admin-authored) schema replaces the built-in one as the response contract;
        # its extra fields survive into model_passes (extra='allow') for the report template.
        schema_kw = response_schema_kwargs(MergedOut, schema_override)
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
            except Exception as exc:  # noqa: BLE001 — translated to retry taxonomy
                raise translate_genai_error(exc) from exc
            if not resp.text:
                raise FatalError("empty merged response")
            return str(resp.text)

        text = await retry_async(
            _call,
            max_attempts=self._retry.retry_max_attempts,
            base=self._retry.retry_base_seconds,
            cap=self._retry.retry_cap_seconds,
            jitter_ratio=self._retry.retry_jitter_ratio,
        )
        return MergedOut.model_validate_json(text)
