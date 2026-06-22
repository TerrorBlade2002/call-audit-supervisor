"""Orchestrator: process one claimed job end-to-end (§8.3).

Dispatch by state → run the handler → apply its outcome to the queue. Any handler
exception is funnelled through the retry policy (backoff + dead-letter). This is the only
place that writes terminal/retry state, so the state machine stays coherent.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.judge.client import JudgeClient
from app.judge.embeddings import Embedder
from app.judge.merged import MergedGenerator
from app.judge.narrative import NarrativeGenerator
from app.judge.routing import RoutingConfig
from app.judge.subjective import SubjectiveGenerator
from app.models import JobState
from app.notifier import NullNotifier, StatusNotifier
from app.orchestration import queue, states
from app.orchestration.handlers import (
    Complete,
    Deferred,
    JobView,
    Park,
    handle_judge,
    handle_transcription,
)
from app.orchestration.queue import ClaimedJob
from app.orchestration.retry import DeadLetter, Retry, decide_failure
from app.orchestration.stubs import SttClient
from app.ratelimit.buckets import Limiters
from app.ratelimit.caps import DailyCap
from app.storage import StorageService

log = structlog.get_logger("orchestration.engine")


@dataclass
class EngineDeps:
    settings: Settings
    limiters: Limiters
    # Built per-session so the cap increment commits in the same tx as the job step.
    make_daily_cap: Callable[[AsyncSession], DailyCap]
    stt: SttClient
    judge: JudgeClient
    embedder: Embedder
    storage: StorageService
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC)
    notifier: StatusNotifier = field(default_factory=NullNotifier)
    # Optional higher tier — set it (config) and escalations route here, no code change.
    escalation_judge: JudgeClient | None = None
    # Agents composed per the call's OPTION inside judge_call (None → that path is unavailable):
    # merged feedback+checklist (FULL), feedback (FEEDBACK_IDEAL), ideal rewriter (FULL/C). The
    # full KB is fed to the agents inside judge_call (no separate distiller dependency).
    merged: MergedGenerator | None = None
    subjective: SubjectiveGenerator | None = None
    rewriter: NarrativeGenerator | None = None

    def routing_config(self) -> RoutingConfig:
        r = self.settings.router
        return RoutingConfig(
            confidence_threshold=r.confidence_threshold,
            min_evidence_chars=r.min_evidence_chars,
            max_escalation_fraction=r.max_escalation_fraction,
        )


def default_make_daily_cap(settings: Settings) -> Callable[[AsyncSession], DailyCap]:
    """Factory: Postgres-backed daily cap bound to the caller's session."""
    from app.ratelimit.caps import PostgresDailyUsageStore

    def _make(session: AsyncSession) -> DailyCap:
        return DailyCap(
            PostgresDailyUsageStore(session), settings.ratelimit.daily_cap_per_portfolio
        )

    return _make


def _next_utc_midnight(now: datetime) -> datetime:
    nxt = now.astimezone(UTC).date() + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=UTC)


def _to_view(job: ClaimedJob) -> JobView:
    return JobView(
        id=job.id,
        call_id=job.call_id,
        portfolio_id=job.portfolio_id,
        transcript_id=job.transcript_id,
        audio_uri=job.audio_uri,
        transcript_uri=job.transcript_uri,
    )


async def process_one(session: AsyncSession, job: ClaimedJob, deps: EngineDeps) -> None:
    """Run a single claimed job's current step and persist the result."""
    state = JobState(job.state)
    view = _to_view(job)
    try:
        if state is JobState.PENDING_TRANSCRIPTION:
            outcome = await handle_transcription(
                view,
                limiters=deps.limiters,
                daily_cap=deps.make_daily_cap(session),
                stt=deps.stt,
                storage=deps.storage,
            )
        elif state is JobState.PENDING_JUDGE:
            outcome = await handle_judge(
                view,
                session=session,
                limiters=deps.limiters,
                stt=deps.stt,
                storage=deps.storage,
                judge=deps.judge,
                embedder=deps.embedder,
                routing_config=deps.routing_config(),
                escalation_judge=deps.escalation_judge,
                merged=deps.merged,
                subjective=deps.subjective,
                rewriter=deps.rewriter,
            )
        else:  # defensive: a non-claimable state should never be claimed
            from app.ratelimit.backoff import FatalError

            raise FatalError(f"unhandled state {state}")
    except Exception as exc:  # noqa: BLE001 — policy decides retry vs dead-letter
        # A handler that failed mid-flush leaves the session in a pending-rollback state;
        # roll back so the failure-policy writes (job_error, attempts, retry/dead-letter)
        # actually commit instead of failing too (which would loop forever with no progress).
        await session.rollback()
        new_state = await _apply_failure(session, job, state, exc, deps)
    else:
        new_state = await _apply_outcome(session, job, state, outcome, deps)

    if new_state is not None:
        await deps.notifier.publish(
            session, portfolio_id=job.portfolio_id, call_id=job.call_id, state=new_state.value
        )


async def _apply_outcome(
    session: AsyncSession, job: ClaimedJob, state: JobState, outcome: object, deps: EngineDeps
) -> JobState | None:
    """Apply a successful step. Returns the state to broadcast, or None for no event."""
    if isinstance(outcome, Park):
        states.assert_transition(state, JobState.AWAITING_TRANSCRIPT)
        await queue.park(session, job.id, outcome.transcript_id)
        return JobState.AWAITING_TRANSCRIPT
    if isinstance(outcome, Complete):
        # Single completing transition per state (judge → DONE; transcription parks instead).
        states.assert_transition(state, JobState.DONE)
        await queue.complete(session, job.id)
        return JobState.DONE
    if isinstance(outcome, Deferred):
        next_at = _next_utc_midnight(deps.now_fn())
        log.info("job.deferred", job_id=str(job.id), reason=outcome.reason)
        await queue.defer(session, job.id, next_attempt_at=next_at, reason=outcome.reason)
        return None  # deferral isn't a user-facing status change
    raise TypeError(f"unknown outcome {outcome!r}")  # pragma: no cover


async def _record_job_error(
    session: AsyncSession, job: ClaimedJob, state: JobState, exc: Exception, *, fatal: bool
) -> None:
    """Persist the full traceback for this failed attempt (debugging observability)."""
    from app.models import JobError

    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    session.add(
        JobError(
            job_id=job.id,
            call_id=job.call_id,
            portfolio_id=job.portfolio_id,
            stage=state.value,
            attempt=job.attempts + 1,
            fatal=fatal,
            error_class=f"{type(exc).__module__}.{type(exc).__qualname__}"[:150],
            message=str(exc)[:4000],
            traceback=tb[-12000:],  # tail-bounded; the root cause is at the end
        )
    )


async def _apply_failure(
    session: AsyncSession, job: ClaimedJob, state: JobState, exc: Exception, deps: EngineDeps
) -> JobState | None:
    """Apply a failed step per the retry policy. Returns FAILED on dead-letter, else None."""
    decision = decide_failure(
        attempts_before=job.attempts,
        max_attempts=job.max_attempts,
        error=exc,
        cfg=deps.settings.ratelimit,
    )
    await _record_job_error(session, job, state, exc, fatal=isinstance(decision, DeadLetter))
    if isinstance(decision, Retry):
        next_at = deps.now_fn() + timedelta(seconds=decision.delay)
        log.warning(
            "job.retry", job_id=str(job.id), state=state, attempt=decision.attempts,
            delay=round(decision.delay, 2), error=str(exc),
        )
        await queue.schedule_retry(
            session, job.id, attempts=decision.attempts, next_attempt_at=next_at,
            last_error=str(exc),
        )
        return None
    # DeadLetter
    log.error(  # alert signal (§16.4): dead-letter count
        "job.dead_letter", job_id=str(job.id), state=state,
        attempts=decision.attempts, reason=decision.reason,
    )
    await queue.dead_letter(
        session, job.id, attempts=decision.attempts, last_error=decision.reason
    )
    return JobState.FAILED
