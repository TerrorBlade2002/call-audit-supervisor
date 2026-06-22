"""Recompute router_overrides from verifier disagreement (§16.3).

Per checklist item, over verified reports containing it, compute the disagreement rate
(WRONG / (CORRECT + WRONG)). Items above ``error_threshold`` with at least ``min_sample``
verifications are force-escalated (written to router_overrides); the routing layer then
always sends them to human review / a higher tier.

This is a coarse v1 using report-level judgements; it sharpens once per-item verifier
feedback is captured. Output is config (router_overrides), redeployed via the same pipeline
and re-validated by the eval gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Report, ReportItem, RouterOverride, Verification


@dataclass
class TuningResult:
    written: int
    cleared: int


async def recompute_overrides(
    session: AsyncSession, *, error_threshold: float = 0.3, min_sample: int = 5
) -> TuningResult:
    # (checklist_item_id, judgement) -> # distinct verified reports containing the item.
    rows = (
        await session.execute(
            select(
                ReportItem.checklist_item_id,
                Verification.judgement,
                func.count(func.distinct(Report.id)),
            )
            .join(Report, Report.id == ReportItem.report_id)
            .join(Verification, Verification.report_id == Report.id)
            .group_by(ReportItem.checklist_item_id, Verification.judgement)
        )
    ).all()

    counts: dict[object, dict[str, int]] = {}
    for item_id, judgement, n in rows:
        counts.setdefault(item_id, {})[judgement] = int(n)

    should_override: dict[object, str] = {}
    for item_id, by_j in counts.items():
        correct = by_j.get("CORRECT", 0)
        wrong = by_j.get("WRONG", 0)
        sample = correct + wrong
        if sample < min_sample:
            continue
        rate = wrong / sample
        if rate >= error_threshold:
            should_override[item_id] = f"verifier disagreement {rate:.0%} over {sample} reviews"

    # Full recompute: clear auto-computed overrides, then write the current set.
    cleared = await session.scalar(select(func.count()).select_from(RouterOverride)) or 0
    await session.execute(delete(RouterOverride))
    for item_id, reason in should_override.items():
        session.add(RouterOverride(checklist_item_id=item_id, reason=reason))
    await session.flush()
    return TuningResult(written=len(should_override), cleared=cleared)
