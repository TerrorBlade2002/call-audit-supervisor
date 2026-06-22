"""Durable queue repository (§8.2–8.5). All queue state lives in the ``jobs`` table.

Concurrency-safety comes from ``FOR UPDATE SKIP LOCKED`` + a lease: many workers can claim
in parallel without stepping on each other, and a worker that dies mid-job leaves an
expired lease that the next claim reclaims (crash recovery for free).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobState


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    call_id: uuid.UUID
    portfolio_id: uuid.UUID
    state: str
    attempts: int
    max_attempts: int
    transcript_id: str | None
    audio_uri: str
    transcript_uri: str | None


_CLAIM_SQL = text(
    """
    WITH claimed AS (
        SELECT id
        FROM jobs
        WHERE state = ANY(:claimable)
          AND next_attempt_at <= now()
          AND (locked_until IS NULL OR locked_until < now())
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT :batch
    )
    UPDATE jobs j
    SET locked_by = :worker,
        locked_until = now() + make_interval(secs => :lease_seconds),
        updated_at = now()
    FROM claimed
    WHERE j.id = claimed.id
    RETURNING j.id, j.call_id, j.portfolio_id, j.state, j.attempts,
              j.max_attempts, j.transcript_id
    """
)


async def claim(
    session: AsyncSession,
    *,
    worker_id: str,
    batch: int,
    lease_seconds: int,
    claimable_states: list[str],
) -> list[ClaimedJob]:
    """Atomically claim up to ``batch`` due, unlocked, claimable jobs with a lease."""
    rows = (
        await session.execute(
            _CLAIM_SQL,
            {
                "claimable": claimable_states,
                "batch": batch,
                "worker": worker_id,
                "lease_seconds": lease_seconds,
            },
        )
    ).all()
    if not rows:
        return []

    # Pull the call URIs the handlers need, in one round-trip.
    ids = [r.id for r in rows]
    calls = {
        c.job_id: c
        for c in (
            await session.execute(
                text(
                    """
                    SELECT j.id AS job_id, c.r2_audio_uri AS audio_uri,
                           c.r2_transcript_uri AS transcript_uri
                    FROM jobs j JOIN calls c ON c.id = j.call_id
                    WHERE j.id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
        ).all()
    }
    return [
        ClaimedJob(
            id=r.id,
            call_id=r.call_id,
            portfolio_id=r.portfolio_id,
            state=r.state,
            attempts=r.attempts,
            max_attempts=r.max_attempts,
            transcript_id=r.transcript_id,
            audio_uri=calls[r.id].audio_uri,
            transcript_uri=calls[r.id].transcript_uri,
        )
        for r in rows
    ]


async def _set(
    session: AsyncSession, job_id: uuid.UUID, assignments: str, params: dict[str, object]
) -> None:
    await session.execute(
        text(
            f"UPDATE jobs SET {assignments}, updated_at = now(), "
            "locked_by = NULL, locked_until = NULL WHERE id = :id"
        ),
        {"id": job_id, **params},
    )


async def park(session: AsyncSession, job_id: uuid.UUID, transcript_id: str) -> None:
    """Submitted to STT → park at AWAITING_TRANSCRIPT (loop won't reclaim it)."""
    await _set(
        session,
        job_id,
        "state = :state, transcript_id = :tid, last_error = NULL",
        {"state": JobState.AWAITING_TRANSCRIPT.value, "tid": transcript_id},
    )


async def complete(session: AsyncSession, job_id: uuid.UUID) -> None:
    await _set(
        session, job_id, "state = :state, last_error = NULL", {"state": JobState.DONE.value}
    )


async def defer(
    session: AsyncSession, job_id: uuid.UUID, *, next_attempt_at: datetime, reason: str
) -> None:
    """Daily cap hit → keep state, retry in the next window. Not a failure."""
    await _set(
        session,
        job_id,
        "next_attempt_at = :naa, last_error = :reason",
        {"naa": next_attempt_at, "reason": reason},
    )


async def schedule_retry(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    attempts: int,
    next_attempt_at: datetime,
    last_error: str,
) -> None:
    """Retryable failure → bump attempts, back off, keep state."""
    await _set(
        session,
        job_id,
        "attempts = :attempts, next_attempt_at = :naa, last_error = :err",
        {"attempts": attempts, "naa": next_attempt_at, "err": last_error},
    )


async def dead_letter(
    session: AsyncSession, job_id: uuid.UUID, *, attempts: int, last_error: str
) -> None:
    """Attempts exhausted (or fatal) → FAILED. Terminal."""
    await _set(
        session,
        job_id,
        "state = :state, attempts = :attempts, last_error = :err",
        {"state": JobState.FAILED.value, "attempts": attempts, "err": last_error},
    )


async def mark_failed(session: AsyncSession, job_id: uuid.UUID, *, reason: str) -> None:
    """Terminal failure without an attempts bump (used by the reconciler on STT error)."""
    await _set(
        session, job_id, "state = :state, last_error = :err",
        {"state": JobState.FAILED.value, "err": reason},
    )


async def set_transcript_uri(session: AsyncSession, call_id: uuid.UUID, uri: str) -> None:
    """Persist the materialized transcript's object key on the call (idempotent step)."""
    await session.execute(
        text("UPDATE calls SET r2_transcript_uri = :uri WHERE id = :id"),
        {"uri": uri, "id": call_id},
    )


async def fail_by_transcript(session: AsyncSession, transcript_id: str, *, reason: str) -> bool:
    """Webhook error path: AWAITING_TRANSCRIPT → FAILED by transcript id. Idempotent."""
    row = (
        await session.execute(
            text(
                """
                UPDATE jobs
                SET state = :failed, locked_by = NULL, locked_until = NULL,
                    last_error = :reason, updated_at = now()
                WHERE transcript_id = :tid AND state = :awaiting
                RETURNING id
                """
            ),
            {
                "failed": JobState.FAILED.value,
                "awaiting": JobState.AWAITING_TRANSCRIPT.value,
                "tid": transcript_id,
                "reason": reason,
            },
        )
    ).first()
    return row is not None


async def touch(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Reset ``updated_at`` (extend the overdue window) without changing state."""
    await session.execute(
        text("UPDATE jobs SET updated_at = now() WHERE id = :id"), {"id": job_id}
    )


async def advance_to_judge_by_transcript(session: AsyncSession, transcript_id: str) -> bool:
    """Webhook/reconciler: AWAITING_TRANSCRIPT → PENDING_JUDGE. Idempotent.

    Returns True if a job advanced, False if none matched (already advanced — a redelivered
    webhook is a safe no-op, §8.4).
    """
    row = (
        await session.execute(
            text(
                """
                UPDATE jobs
                SET state = :judge, locked_by = NULL, locked_until = NULL,
                    next_attempt_at = now(), updated_at = now(), last_error = NULL
                WHERE transcript_id = :tid AND state = :awaiting
                RETURNING id
                """
            ),
            {
                "judge": JobState.PENDING_JUDGE.value,
                "awaiting": JobState.AWAITING_TRANSCRIPT.value,
                "tid": transcript_id,
            },
        )
    ).first()
    return row is not None


async def find_overdue_awaiting(
    session: AsyncSession, *, overdue_seconds: int
) -> list[tuple[uuid.UUID, str | None]]:
    """Parked transcripts whose webhook is overdue → reconciler polls them (§8.5)."""
    rows = (
        await session.execute(
            text(
                """
                SELECT id, transcript_id FROM jobs
                WHERE state = :awaiting
                  AND updated_at < now() - make_interval(secs => :overdue)
                """
            ),
            {"awaiting": JobState.AWAITING_TRANSCRIPT.value, "overdue": overdue_seconds},
        )
    ).all()
    return [(r.id, r.transcript_id) for r in rows]


async def enqueue(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    portfolio_id: uuid.UUID,
    max_attempts: int,
) -> uuid.UUID:
    """Create a job in PENDING_TRANSCRIPTION, due immediately. Used by ingestion/tests."""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO jobs (call_id, portfolio_id, state, max_attempts, next_attempt_at)
                VALUES (:call_id, :pid, :state, :max_attempts, now())
                RETURNING id
                """
            ),
            {
                "call_id": call_id,
                "pid": portfolio_id,
                "state": JobState.PENDING_TRANSCRIPTION.value,
                "max_attempts": max_attempts,
            },
        )
    ).first()
    assert row is not None
    return uuid.UUID(str(row.id))
