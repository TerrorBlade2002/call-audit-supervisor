"""Verification persistence + agreement metric (Phase 7)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, Report, Verification


async def submit_verification(
    session: AsyncSession,
    *,
    report_id: uuid.UUID,
    verifier_id: uuid.UUID,
    judgement: str,
    notes: str | None,
) -> Verification:
    verification = Verification(
        report_id=report_id, verifier_id=verifier_id, judgement=judgement, notes=notes
    )
    session.add(verification)
    await session.flush()
    return verification


async def agreement_stats(session: AsyncSession, portfolio_id: uuid.UUID) -> dict[str, object]:
    """Judge↔verifier agreement for a portfolio (§16.2 gold set signal).

    CORRECT = judge agreed with the verifier. Rate = correct / (correct + wrong); CANT_SAY
    is excluded from the denominator (it's not a disagreement signal).
    """
    rows = (
        await session.execute(
            select(Verification.judgement, func.count())
            .join(Report, Report.id == Verification.report_id)
            .join(Call, Call.id == Report.call_id)
            .where(Call.portfolio_id == portfolio_id)
            .group_by(Verification.judgement)
        )
    ).all()
    counts = {j: int(n) for j, n in rows}
    correct = counts.get("CORRECT", 0)
    wrong = counts.get("WRONG", 0)
    cant = counts.get("CANT_SAY", 0)
    decided = correct + wrong
    return {
        "total": correct + wrong + cant,
        "correct": correct,
        "wrong": wrong,
        "cant_say": cant,
        "agreement_rate": (correct / decided) if decided else None,
    }
