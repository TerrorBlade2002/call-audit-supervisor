"""Operational metrics for the §16.4 dashboards + alerts.

Surfaces the signals the PRD calls out: queue depth by state, oldest job age, dead-letter
count, escalation fraction, judge↔verifier agreement, and daily-cap usage vs. cap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyUsage, JobState, ReportItem, Verification


async def collect_metrics(session: AsyncSession) -> dict[str, Any]:
    # Queue depth by state.
    depth_rows = (
        await session.execute(text("SELECT state, count(*) FROM jobs GROUP BY state"))
    ).all()
    queue_depth = {state: int(n) for state, n in depth_rows}

    # Oldest non-terminal job age (seconds) — liveness signal.
    oldest = await session.scalar(
        text(
            "SELECT EXTRACT(EPOCH FROM (now() - min(created_at))) FROM jobs "
            "WHERE state NOT IN ('DONE','FAILED')"
        )
    )

    dead_letters = queue_depth.get(JobState.FAILED.value, 0)

    # Escalation fraction across all report items (circuit-breaker / routing health).
    total_items = await session.scalar(select(func.count()).select_from(ReportItem)) or 0
    flagged = (
        await session.scalar(
            select(func.count()).select_from(ReportItem).where(ReportItem.needs_human_review.is_(True))
        )
        or 0
    )
    escalation_fraction = (flagged / total_items) if total_items else 0.0

    # Judge↔verifier agreement (overall).
    j_rows = (
        await session.execute(
            select(Verification.judgement, func.count()).group_by(Verification.judgement)
        )
    ).all()
    jcounts = {j: int(n) for j, n in j_rows}
    decided = jcounts.get("CORRECT", 0) + jcounts.get("WRONG", 0)
    agreement = (jcounts.get("CORRECT", 0) / decided) if decided else None

    # Daily cap usage today vs. cap.
    today = datetime.now(UTC).date()
    used_today = (
        await session.scalar(
            select(func.coalesce(func.sum(DailyUsage.calls_submitted), 0)).where(
                DailyUsage.day == today
            )
        )
        or 0
    )

    return {
        "queue_depth": queue_depth,
        "oldest_job_age_sec": float(oldest) if oldest is not None else 0.0,
        "dead_letters": dead_letters,
        "escalation_fraction": round(escalation_fraction, 4),
        "agreement_rate": agreement,
        "daily_cap_per_portfolio": settings.ratelimit.daily_cap_per_portfolio,
        "calls_submitted_today": int(used_today),
    }
