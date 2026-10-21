"""Per-state step handlers. Each takes a claimed job + dependencies, returns a StepOutcome.

Handlers contain the *work*; the engine applies the outcome to the queue. Handlers signal
failure by raising (RetryableError/RateLimitError/FatalError); the engine's retry policy
turns that into a backoff or a dead-letter. Rate limits and the daily cap are enforced
here, at the point of the external call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.judge.client import JudgeClient
from app.judge.embeddings import Embedder
from app.judge.gemini import AudioRef, AudioUploader, audio_mime_for
from app.judge.merged import MergedGenerator
from app.judge.narrative import NarrativeGenerator
from app.judge.options import ProcessingOption, needs_judge
from app.judge.routing import RoutingConfig
from app.judge.service import judge_call
from app.judge.subjective import SubjectiveGenerator
from app.models import Call, Portfolio
from app.orchestration import queue
from app.orchestration.stubs import SttClient
from app.ratelimit.backoff import FatalError
from app.ratelimit.buckets import Limiters
from app.ratelimit.caps import DailyCap
from app.storage import StorageService
from app.stt.redaction import PiiMode, parse_pii_mode

log = structlog.get_logger("orchestration.handlers")


async def get_portfolio_pii_mode(session: AsyncSession, portfolio_id: uuid.UUID) -> PiiMode:
    """This portfolio's redaction level, read at processing time.

    Deliberately a live lookup rather than a value copied onto the call at upload: the
    product rule is "change the setting, and every call processed after it follows the new
    setting". A missing portfolio falls back to OFF, matching the column default.
    """
    raw = await session.scalar(select(Portfolio.pii_mode).where(Portfolio.id == portfolio_id))
    return parse_pii_mode(raw)


async def _redact_stored_recording(
    job: JobView, stt: SttClient, storage: StorageService
) -> None:
    """Replace the stored recording with the provider's redacted (beeped) copy.

    Runs only AFTER judging, so the multimodal judge still heard the original audio — which
    is the whole point of keeping it until now (it is what lets the judge grade "did the
    collector identify the company?" when the transcript says [ORGANIZATION]).

    Written to the SAME object key, so the call's stored URI stays valid and no orphan is
    left behind. Best-effort: if the provider has no redacted copy, the transcript is still
    redacted and the retention sweep still deletes the audio on schedule, so failing the
    whole call here would cost more than it protects.
    """
    getter = getattr(stt, "get_redacted_audio_url", None)
    if getter is None:
        return
    try:
        url = await getter(transcript_id=job.transcript_id)
        if not url:
            log.warning("judge.redacted_audio_unavailable", call_id=str(job.call_id))
            return
        async with httpx.AsyncClient(timeout=300.0) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            data = resp.content
        if not data:
            return
        await storage.put_recording(job.audio_uri, data, audio_mime_for(job.audio_uri))
        log.info("judge.recording_redacted", call_id=str(job.call_id), bytes=len(data))
    except Exception as exc:  # noqa: BLE001 — see docstring: best-effort by design
        log.warning(
            "judge.redacted_audio_failed", call_id=str(job.call_id), error=str(exc)[:200]
        )


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
    session: AsyncSession,
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

    # The portfolio's current redaction level, read live — not snapshotted onto the call —
    # so an admin's change governs every call submitted after it. redact_pii_audio has to be
    # decided here: the provider only builds a redacted audio file for a request that asked
    # for one, so it cannot be bolted on after the transcript comes back.
    mode = await get_portfolio_pii_mode(session, job.portfolio_id)
    audio_url = storage.presign_audio_get(job.audio_uri)
    async with limiters.assemblyai.guard():
        transcript_id = await stt.submit(
            call_id=job.call_id,
            audio_url=audio_url,
            redact_pii=mode.redacts_transcript,
            redact_pii_audio=mode.redacts_recording,
        )
    return Park(transcript_id=transcript_id)


async def _resolve_audio(
    job: JobView, storage: StorageService, uploader: AudioUploader | None
) -> AudioRef | None:
    """The AudioRef the agents receive: Files API reference when possible, else inline.

    Uploaded once per job — every agent in the pipeline (merged/checklist + escalation)
    references the same file instead of re-sending the bytes. Any upload failure falls
    back to inline bytes, which is exactly the pre-Files-API behavior.
    """
    audio_bytes = await storage.get_audio_bytes(job.audio_uri)
    if not audio_bytes:
        return None  # unavailable → agents degrade gracefully to text-only
    mime = audio_mime_for(job.audio_uri)
    if uploader is not None:
        try:
            return await uploader.upload(audio_bytes, mime)
        except Exception as exc:  # noqa: BLE001 — fallback keeps the job moving
            log.warning(
                "judge.files_api_fallback_inline",
                call_id=str(job.call_id),
                bytes=len(audio_bytes),
                error=str(exc)[:200],
            )
    return AudioRef(audio_bytes, mime)


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
    audio_uploader: AudioUploader | None = None,
) -> StepOutcome:
    """PENDING_JUDGE → materialize the transcript (once), then run the OPTION's pipeline, DONE.

    Transcript materialization (fetch from STT + store to R2 + record the URI) is the
    idempotent preamble: skipped if already stored, so re-running PENDING_JUDGE never
    re-fetches or double-charges. The OPTION on the call then drives the pipeline — RAW_ONLY
    ends right here (transcript only, no LLM, no report). Re-judging is safe (keyed by call_id).
    """
    mode = await get_portfolio_pii_mode(session, job.portfolio_id)
    if job.transcript_uri is None:
        if not job.transcript_id:
            raise FatalError("PENDING_JUDGE without a transcript_id")
        async with limiters.assemblyai.guard():
            transcript = await stt.fetch_transcript(
                transcript_id=job.transcript_id,
                require_redacted=mode.redacts_transcript,
            )
        # Last gate before anything is written: with redaction enabled, only a transcript the
        # provider confirmed it redacted may be persisted. The client checks this too; this
        # second check is here because THIS is the line that writes to object storage, and a
        # future STT client must not be able to bypass the guarantee by omitting its own.
        if mode.redacts_transcript and not transcript.redacted:
            raise FatalError(
                f"refusing to store an unredacted transcript for call {job.call_id} "
                f"(portfolio pii_mode={mode.value} but the transcript is not marked redacted)"
            )
        uri = await storage.put_transcript(job.call_id, transcript)
        await queue.set_transcript_uri(session, job.call_id, uri)
    else:
        transcript = await storage.get_transcript(job.call_id)

    call = await session.get(Call, job.call_id)
    option = ProcessingOption(call.option) if call and call.option else ProcessingOption.FULL
    if not needs_judge(option):
        # RAW_ONLY — no LLM, no report. The recording still gets swapped for the redacted
        # copy when the portfolio asks for it; there is no judge pass to wait for here.
        if mode.redacts_recording:
            await _redact_stored_recording(job, stt, storage)
        return Complete()  # transcript stored, nothing else to produce

    # Multimodal: hand the recording audio to the agents (None when unavailable → text-only).
    # Files API when available (uploaded once, referenced by every agent), inline as fallback.
    audio = await _resolve_audio(job, storage, audio_uploader)
    kb_doc_ids = (
        [uuid.UUID(str(x)) for x in call.kb_doc_ids] if call and call.kb_doc_ids else None
    )

    try:
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
    finally:
        # Post-job hygiene: drop the uploaded file (best-effort — auto-expires in 48 h).
        # Kept on retry-able failures too: the retry re-uploads, which is simpler and safer
        # than tracking cross-attempt file state on the job row.
        if audio_uploader is not None and audio is not None and audio.file_name:
            await audio_uploader.delete(audio.file_name)
    # Only now, with the judge finished and the report written, is it safe to swap the stored
    # recording for the beeped copy: up to this point the judge needed the original audio.
    # Deliberately outside the try/finally — a failed judge will be retried and must still
    # find the unredacted audio.
    if mode.redacts_recording:
        await _redact_stored_recording(job, stt, storage)
    return Complete()
