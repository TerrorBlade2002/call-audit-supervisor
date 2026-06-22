"""Retention sweep (FR4): purge recordings + transcripts + reports older than N days.

R2 native lifecycle rules are the ideal mechanism (zero compute), but our object-scoped R2
token can't set bucket lifecycle, so retention is enforced from the app: find calls past
the cutoff, delete their recording + transcript objects from R2, then delete the call row —
the DB cascade removes the job, report, items, verifications and objections with it.

The knowledge base is exempt: it's reference data, kept forever (only the per-call
artifacts age out).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call
from app.storage import StorageService

log = structlog.get_logger("retention")


async def run_once(
    session: AsyncSession,
    *,
    storage: StorageService,
    cutoff_days: int,
    batch: int = 200,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Purge one batch of calls older than ``cutoff_days``. Returns the number removed."""
    cutoff = now_fn() - timedelta(days=cutoff_days)
    rows = (
        await session.execute(
            select(Call.id, Call.r2_audio_uri)
            .where(Call.created_at < cutoff)
            .order_by(Call.created_at)
            .limit(batch)
        )
    ).all()

    purged = 0
    for call_id, audio_uri in rows:
        # Best-effort object deletes; the row removal is the source of truth regardless.
        await storage.delete_recording(audio_uri)
        await storage.delete_transcript(call_id)
        await storage.delete_report(call_id)
        call = await session.get(Call, call_id)
        if call is not None:
            await session.delete(call)  # cascade: job, report, items, verifications
            purged += 1
    if purged:
        await session.commit()
        log.info("retention.purged", count=purged, cutoff_days=cutoff_days)
    return purged
