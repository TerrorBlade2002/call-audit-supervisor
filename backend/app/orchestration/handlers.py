"""Per-state step handlers. Each takes a claimed job + dependencies, returns a StepOutcome.

Handlers contain the *work*; the engine applies the outcome to the queue. Handlers signal
failure by raising (RetryableError/RateLimitError/FatalError); the engine's retry policy
turns that into a backoff or a dead-letter. Rate limits and the daily cap are enforced
here, at the point of the external call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.judge.client import JudgeClient
from app.judge.embeddings import Embedder
from app.judge.gemini import AudioRef, audio_mime_for
from app.judge.merged import MergedGenerator
from app.judge.narrative import NarrativeGenerator
from app.judge.options import ProcessingOption, needs_judge
from app.judge.routing import RoutingConfig
from app.judge.service import judge_call
from app.judge.subjective import SubjectiveGenerator
from app.models import Call
from app.orchestration import queue
from app.orchestration.stubs import SttClient
from app.ratelimit.backoff import FatalError
from app.ratelimit.buckets import Limiters
from app.ratelimit.caps import DailyCap
from app.storage import StorageService


@dataclass(frozen=True)
class Park:
    """Submitted to STT; park the job at AWAITING_TRANSCRIPT with this transcript id."""

    transcript_id: str


@dataclass(frozen=True)
class Complete:
    """Step finished; advance to the terminal/next state (judge → DONE)."""


@dataclass(frozen=True)
class Deferred:
    """Daily cap reached; re-queue for the next window (not a failure)."""

    reason: str


StepOutcome = Park | Complete | Deferred


@dataclass(frozen=True)
class JobView:
    """The fields a handler needs from a claimed job (decoupled from the ORM row)."""

    id: uuid.UUID
    call_id: uuid.UUID
    portfolio_id: uuid.UUID
    transcript_id: str | None
    audio_uri: str
    transcript_uri: str | None


# Conservative per-judge-call token estimate for TPM accounting (see RATE_LIMITS doc).
JUDGE_TOKEN_ESTIMATE = 16_000


async def handle_transcription(
    job: JobView,
    *,
    limiters: Limiters,
    daily_cap: DailyCap,
    stt: SttClient,
    storage: StorageService,
) -> StepOutcome:
    """PENDING_TRANSCRIPTION → submit audio to STT, park awaiting the webhook.

    Idempotency (§7.1/§8.3): if a transcript id already exists, never resubmit (no double
    charge) — just re-park. Daily cap is checked *before* submitting; over-cap defers. The
    audio is handed to STT as a short-TTL presigned GET URL (STT downloads it from R2).
    """
    if job.transcript_id:
        return Park(transcript_id=job.transcript_id)

    # Guard: a missing object yields a clear message instead of an opaque provider
    # "download error" later (e.g. an upload that never landed in storage).
    if not storage.recording_exists(job.audio_uri):
        raise FatalError(
            "Recording wasn't found in storage — the upload didn't complete. Re-upload the file."
        )

    decision = await daily_cap.check_and_reserve(job.portfolio_id)
    if decision.deferred:
        return Deferred(reason=f"daily limit reached ({decision.used}/{decision.cap})")

    audio_url = storage.presign_audio_get(job.audio_uri)
    async with limiters.assemblyai.guard():
        transcript_id = await stt.submit(call_id=job.call_id, audio_url=audio_url)
    return Park(transcript_id=transcript_id)


async def handle_judge(
    job: JobView,
    *,
    session: AsyncSession,
    limiters: Limiters,
    stt: SttClient,
    storage: StorageService,
    judge: JudgeClient,
    embedder: Embedder,
    routing_config: RoutingConfig,
    escalation_judge: JudgeClient | None = None,
    merged: MergedGenerator | None = None,
    subjective: SubjectiveGenerator | None = None,
    rewriter: NarrativeGenerator | None = None,
) -> StepOutcome:
    """PENDING_JUDGE → materialize the transcript (once), then run the OPTION's pipeline, DONE.

    Transcript materialization (fetch from STT + store to R2 + record the URI) is the
    idempotent preamble: skipped if already stored, so re-running PENDING_JUDGE never
    re-fetches or double-charges. The OPTION on the call then drives the pipeline — RAW_ONLY
    ends right here (transcript only, no LLM, no report). Re-judging is safe (keyed by call_id).
    """
    if job.transcript_uri is None:
        if not job.transcript_id:
            raise FatalError("PENDING_JUDGE without a transcript_id")
        async with limiters.assemblyai.guard():
            transcript = await stt.fetch_transcript(transcript_id=job.transcript_id)
        uri = await storage.put_transcript(job.call_id, transcript)
        await queue.set_transcript_uri(session, job.call_id, uri)
    else:
        transcript = await storage.get_transcript(job.call_id)

    call = await session.get(Call, job.call_id)
    option = ProcessingOption(call.option) if call and call.option else ProcessingOption.FULL
    if not needs_judge(option):
        return Complete()  # RAW_ONLY — transcript stored, nothing else to produce

    # Multimodal: hand the recording audio to the agents (None when unavailable → text-only).
    audio_bytes = await storage.get_audio_bytes(job.audio_uri)
    audio = AudioRef(audio_bytes, audio_mime_for(job.audio_uri)) if audio_bytes else None
    kb_doc_ids = (
        [uuid.UUID(str(x)) for x in call.kb_doc_ids] if call and call.kb_doc_ids else None
    )

    async with limiters.gemini.guard(est_tokens=JUDGE_TOKEN_ESTIMATE):
        await judge_call(
            session,
            call_id=job.call_id,
            portfolio_id=job.portfolio_id,
            agent_id=call.agent_id if call else None,
            transcript=transcript,
            option=option,
            judge=judge,
            merged_gen=merged,
            subjective_gen=subjective,
            rewriter_gen=rewriter,
            embedder=embedder,
            routing_config=routing_config,
            escalation_judge=escalation_judge,
            audio=audio,
            checklist_id=call.checklist_id if call else None,
            kb_doc_ids=kb_doc_ids,
        )
    return Complete()
