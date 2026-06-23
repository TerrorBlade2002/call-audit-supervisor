"""Lazy narrative generation (§7.4, §12) — coaching, compliance, feedback, and the
**evidence-gated Ideal Rewritten Conversation**.

Anti-bias design (the user's core requirement): the rewriter does NOT decide what was
wrong. It receives the judge's *evidenced* findings and may diverge from the original ONLY
at the decision points tied to a FAIL with a cited quote. If there are no FAILs, it returns
the original conversation essentially unchanged and states the call was already ideal — it
will not manufacture improvements to a clean call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from google.genai import types

from app.config import RateLimitSettings
from app.judge.gemini import response_schema_kwargs, translate_genai_error
from app.judge.prompts import (
    IMPARTIALITY_DIRECTIVE,
    REWRITER_AGENT_PROMPT,
    REWRITER_OUTPUT_DIRECTIVE,
)
from app.judge.schema import IdealOut
from app.ratelimit.backoff import FatalError, retry_async
from app.stt import Transcript


@dataclass(frozen=True)
class VerdictSummary:
    section: str
    text: str
    answer: str | None
    comment: str | None
    evidence_quote: str | None = None


class NarrativeGenerator(Protocol):
    async def generate(
        self,
        *,
        transcript: Transcript,
        verdicts: list[VerdictSummary],
        subjective: dict[str, Any],
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


def _transcript_text(transcript: Transcript) -> str:
    if transcript.utterances:
        return "\n".join(f"[{u.speaker}] {u.text}" for u in transcript.utterances)
    return transcript.text


def _system(body: str | None) -> str:
    """System instruction = (custom body or default) + the code-owned directives (kept always)."""
    return (body or REWRITER_AGENT_PROMPT) + REWRITER_OUTPUT_DIRECTIVE + IMPARTIALITY_DIRECTIVE


class StubNarrative:
    """Deterministic rewriter (Agent 3) for tests/dev (no LLM). Ideal conversation only."""

    async def generate(
        self,
        *,
        transcript: Transcript,
        verdicts: list[VerdictSummary],
        subjective: dict[str, Any],
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fails = [v.text for v in verdicts if v.answer == "FAIL"]
        return {
            "already_ideal": not fails,
            "ideal_conversation": [
                {"speaker": "Agent", "text": "Thank you for calling. All calls are recorded."},
                {"speaker": "Consumer", "text": "..."},
            ],
        }


class GeminiNarrative:
    """Gemini narrative generator on the google-genai SDK (Developer API)."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        retry: RateLimitSettings,
        thinking_level: str = "HIGH",
    ) -> None:
        self._client = client
        self._model = model
        self._retry = retry
        self._thinking = thinking_level

    def _prompt(
        self,
        transcript: Transcript,
        verdicts: list[VerdictSummary],
        subjective: dict[str, Any],
        kb: str | None,
    ) -> str:
        findings = "\n".join(
            f"- [{v.section}] {v.text}: {v.answer or 'N/A'} | evidence: "
            f"{v.evidence_quote or '—'} | note: {v.comment or ''}"
            for v in verdicts
        )
        n_fail = sum(1 for v in verdicts if v.answer == "FAIL")
        subj = subjective.get("summary") or subjective.get("coaching") or ""
        kb_block = (
            f"KNOWLEDGE BASE (Everest operational documents):\n{kb}\n\n" if kb else ""
        )
        return (
            f"{kb_block}"
            f"SUBJECTIVE FEEDBACK (Agent 1): {subj}\n\n"
            f"EVIDENCED FINDINGS ({n_fail} FAIL):\n{findings}\n\n"
            f"TRANSCRIPT (data):\n{_transcript_text(transcript)}\n\n"
            "Return JSON: {already_ideal(bool), ideal_conversation:[{speaker,text}]} only."
        )

    async def generate(
        self,
        *,
        transcript: Transcript,
        verdicts: list[VerdictSummary],
        subjective: dict[str, Any],
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schema_kw = response_schema_kwargs(IdealOut, schema_override)
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
                    contents=self._prompt(transcript, verdicts, subjective, kb),
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001
                raise translate_genai_error(exc) from exc
            if not resp.text:
                raise FatalError("empty rewriter response")
            return str(resp.text)

        text = await retry_async(
            _call,
            max_attempts=self._retry.retry_max_attempts,
            base=self._retry.retry_base_seconds,
            cap=self._retry.retry_cap_seconds,
            jitter_ratio=self._retry.retry_jitter_ratio,
        )
        return IdealOut.model_validate_json(text).model_dump()
