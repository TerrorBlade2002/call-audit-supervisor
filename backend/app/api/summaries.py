"""Batch smoke-summary endpoints (§ batch triage). Deterministic, on-demand from the DB.

Checklist summary → JSON (in-app triage) + CSV. Feedback summary → standalone HTML (download).
All REPORT_VIEW-gated and scoped to one upload batch.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import AuthContext, authorize
from app.db import get_session
from app.rbac import Action
from app.reports.summary import checklist_summary, checklist_summary_csv, feedback_summary_html
from app.schemas import ChecklistSummaryOut

router = APIRouter(tags=["summaries"])


@router.get(
    "/portfolios/{pid}/batches/{bid}/checklist-summary", response_model=ChecklistSummaryOut
)
async def batch_checklist_summary(
    pid: uuid.UUID,
    bid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChecklistSummaryOut:
    return ChecklistSummaryOut(**await checklist_summary(session, pid, bid))


@router.get("/portfolios/{pid}/batches/{bid}/checklist-summary.csv")
async def batch_checklist_summary_csv(
    pid: uuid.UUID,
    bid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    csv_text = checklist_summary_csv(await checklist_summary(session, pid, bid))
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="batch_{str(bid)[:8]}_summary.csv"'
        },
    )


@router.get("/portfolios/{pid}/batches/{bid}/feedback-summary.html")
async def batch_feedback_summary_html(
    pid: uuid.UUID,
    bid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    html_doc = await feedback_summary_html(session, pid, bid)
    return Response(
        content=html_doc,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="batch_{str(bid)[:8]}_feedback.html"'
        },
    )
