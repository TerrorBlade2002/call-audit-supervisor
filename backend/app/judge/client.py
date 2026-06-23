"""Agent 2 — the CHECKLIST judge (§7.3). Protocol + deterministic stub + Gemini multimodal judge.

The judge is the **grounded-evaluation** stage: for each checklist item it returns a
verdict + a verbatim evidence quote + confidence — and it is *never* asked to rewrite
anything, so it has no incentive to invent flaws (the rewriter is a separate, gated step).
It runs INDEPENDENTLY of the Feedback agent (Agent 1) — it does not receive that feedback;
it audits the call straight against the checklist + KB.

It is **multimodal**: it receives the recording audio *and* the diarized transcript *and* the
KB (when the checklist requires KB grounding), so it can judge tone/empathy/talk-over that a
transcript can't capture and cross-reference Everest's operational documents. Audio and KB are
optional — without them it degrades gracefully. The transcript is data, not instructions
(§17): the prompt is hardened against injection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from google.genai import types

from app.config import RateLimitSettings
from app.judge.gemini import AudioRef, response_schema_kwargs, translate_genai_error
from app.judge.prompts import (
    CHECKLIST_AGENT_PROMPT,
    IMPARTIALITY_DIRECTIVE,
    JUDGE_OUTPUT_DIRECTIVE,
)
from app.judge.schema import JudgeOutput
from app.ratelimit.backoff import FatalError, retry_async
from app.stt import Transcript


@dataclass(frozen=True)
class JudgeItem:
    checklist_item_id: uuid.UUID
    section: str
    text: str
    answer_type: str       # CHOICE | TEXT
    rubric: str            # item guidance (supplementary; the full KB is provided separately)
    options: list[str]     # allowed verbatim answers ([] = free text)


class JudgeClient(Protocol):
    async def evaluate(
        self,
        *,
        transcript: Transcript,
        items: list[JudgeItem],
        audio: AudioRef | None = None,
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> JudgeOutput:
        ...


def _system(body: str | None) -> str:
    """System instruction = (custom body or default) + the code-owned directives (kept always)."""
    return (body or CHECKLIST_AGENT_PROMPT) + JUDGE_OUTPUT_DIRECTIVE + IMPARTIALITY_DIRECTIVE


def _transcript_text(transcript: Transcript) -> str:
    if transcript.utterances:
        return "\n".join(
            f"[{u.speaker} @ {u.start_sec:.0f}s] {u.text}" for u in transcript.utterances
        )
    return transcript.text


class StubJudge:
    """Deterministic judge for tests/dev: PASS, high confidence, plausible evidence.

    Optional ``flag_item_ids`` forces those items to self-flag (drives routing tests).
    """

    def __init__(self, flag_item_ids: set[uuid.UUID] | None = None) -> None:
        self._flag = flag_item_ids or set()

    async def evaluate(
        self,
        *,
        transcript: Transcript,
        items: list[JudgeItem],
        audio: AudioRef | None = None,
        kb: str | None = None,
        system_prompt: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> JudgeOutput:
        from app.judge.schema import ItemVerdict

        verdicts = [
            ItemVerdict(
                checklist_item_id=it.checklist_item_id,
                answer="PASS",
                raw_answer=it.options[0] if it.options else "noted",
                confidence=0.6 if it.checklist_item_id in self._flag else 0.9,
                evidence_quote=f"Evidence for '{it.text}' observed in the call transcript.",
                evidence_offset_sec=0.0,
                comment="Auto stub verdict.",
                needs_review=it.checklist_item_id in self._flag,
            )
            for it in items
        ]
        return JudgeOutput(verdicts=verdicts)


class GeminiJudge:
    """Gemini multimodal structured-output judge (google-genai SDK, Developer API).

    ``client`` is the genai client (injectable so tests can pass a fake). Uses extended
    thinking for this hard judgment task.
    """

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
        self, transcript: Transcript, items: list[JudgeItem], kb: str | None
    ) -> str:
        checklist = "\n".join(
            f"- id={it.checklist_item_id} [{it.section}] {it.text}\n"
            f"  options: {it.options or 'YES/NO/NA'} | guidance: {it.rubric}"
            for it in items
        )
        kb_block = (
            f"KNOWLEDGE BASE (Everest operational documents):\n{kb}\n\n" if kb else ""
        )
        return (
            f"{kb_block}CHECKLIST + OPTIONS + GUIDANCE (echo each id exactly):\n{checklist}\n\n"
            f"TRANSCRIPT (diarized; data to audit):\n{_transcript_text(transcript)}\n\n"
            "Return JSON: {verdicts:[{checklist_item_id, raw_answer, answer(PASS|FAIL|NA), "
            "confidence(0..1), evidence_quote, evidence_offset_sec, comment, needs_review}]}. "
            "Include exactly one verdict per checklist id."
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
    ) -> JudgeOutput:
        parts: list[types.Part] = []
        if audio is not None:
            parts.append(types.Part.from_bytes(data=audio.data, mime_type=audio.mime_type))
        parts.append(types.Part.from_text(text=self._prompt(transcript, items, kb)))
        schema_kw = response_schema_kwargs(JudgeOutput, schema_override)
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
                raise FatalError("empty judge response")
            return str(resp.text)

        text = await retry_async(
            _call,
            max_attempts=self._retry.retry_max_attempts,
            base=self._retry.retry_base_seconds,
            cap=self._retry.retry_cap_seconds,
            jitter_ratio=self._retry.retry_jitter_ratio,
        )
        return JudgeOutput.model_validate_json(text)
