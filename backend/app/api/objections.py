"""Portfolio objection views (§7.5/FR10): the never-cleared clusters + an append-only log."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import AuthContext, authorize
from app.db import get_session
from app.judge.clustering import cluster_objections
from app.models import Agent, Call, Objection, Report
from app.rbac import Action
from app.schemas import ObjectionClusterOut, ObjectionLogOut

router = APIRouter(tags=["objections"])


async def _objection_log(session: AsyncSession, pid: uuid.UUID) -> list[ObjectionLogOut]:
    """Every objection surfaced by the feedback agent, newest first — call id, agent, whether it
    was handled (pass/fail), upload time, and the objection text."""
    rows = (
        await session.execute(
            select(
                Report.call_id, Call.created_at, Objection.text,
                Report.agent_name, Agent.name, Objection.cleared,
            )
            .join(Report, Report.id == Objection.report_id)
            .join(Call, Call.id == Report.call_id)
            .join(Agent, Agent.id == Call.agent_id, isouter=True)
            .where(Call.portfolio_id == pid)
            .order_by(Call.created_at.desc())
        )
    ).all()
    return [
        ObjectionLogOut(
            call_id=c, created_at=t, text=x, agent=(agent_name or folder), cleared=cleared
        )
        for c, t, x, agent_name, folder, cleared in rows
    ]


@router.get("/portfolios/{pid}/objections", response_model=list[ObjectionClusterOut])
async def list_objection_clusters(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ObjectionClusterOut]:
    clusters = await cluster_objections(session, pid)
    return [
        ObjectionClusterOut(
            representative_text=c.representative_text,
            count=c.count,
            cleared_count=c.cleared_count,
            never_cleared=c.never_cleared,
            examples=c.examples,
        )
        for c in clusters
    ]


@router.get("/portfolios/{pid}/objection-log", response_model=list[ObjectionLogOut])
async def list_objection_log(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ObjectionLogOut]:
    return await _objection_log(session, pid)


@router.get("/portfolios/{pid}/objection-log.csv")
async def export_objection_log_csv(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    rows = await _objection_log(session, pid)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Call ID", "Agent", "Status", "Uploaded", "Objection"])
    for r in rows:
        writer.writerow([
            str(r.call_id), r.agent or "—", "PASS" if r.cleared else "FAIL",
            r.created_at.isoformat(), r.text,
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="objection_log.csv"'},
    )
