"""Admin/observability endpoints (§16.4). Org-ADMIN only."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import AuthContext, authorize_org
from app.db import get_session
from app.models import Agent, AuditLog, Call, Job, JobError, Report, User
from app.observability.metrics import collect_metrics
from app.rbac import Action

router = APIRouter(tags=["admin"])

# Which agents run for each processing OPTION — the deterministic pipeline flow.
_AGENTS_BY_OPTION = {
    "FULL": ["Merged agent (feedback + checklist)", "Ideal-conversation agent"],
    "FEEDBACK_IDEAL": ["Feedback agent", "Ideal-conversation agent"],
    "CHECKLIST_ONLY": ["Checklist agent"],
    "RAW_ONLY": [],
}

_ACTION_LABEL = {
    "portfolio.create": "created a portfolio",
    "portfolio.delete": "deleted a portfolio",
    "agent.create": "added an agent",
    "agent.rename": "renamed an agent",
    "agent.delete": "removed an agent",
    "calls.register": "uploaded recordings",
    "call.delete": "deleted a recording",
    "kb.upload": "added KB document(s)",
    "kb.register": "added KB document(s)",
    "kb.delete": "deleted a KB document",
    "checklist.create": "created a checklist",
    "checklist.update": "edited the checklist",
    "report.save": "saved a report review",
    "verification.submit": "verified a report",
    "portfolio.member.assign": "assigned a member",
}


def _describe(action: str, meta: dict[str, Any] | None) -> str:
    label = _ACTION_LABEL.get(action, action)
    m = meta or {}
    if action == "agent.rename" and m.get("name"):
        label = f"renamed an agent to “{m['name']}”"
    elif action in ("calls.register", "kb.upload", "kb.register") and m.get("count"):
        label = f"{label.replace('(s)', '')} ×{m['count']}"
    elif action == "kb.delete" and m.get("filename"):
        label = f"deleted KB document “{m['filename']}”"
    elif action == "portfolio.create" and m.get("name"):
        label = f"created portfolio “{m['name']}”"
    return label


@router.get("/admin/metrics")
async def metrics(
    # USER_MANAGE is ADMIN-only in the matrix → this gates the endpoint to org admins.
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await collect_metrics(session)


@router.get("/admin/job-errors")
async def job_errors(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    call_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Recent job failures with full traceback (debugging). Optionally filter by call."""
    stmt = select(JobError).order_by(JobError.created_at.desc()).limit(limit)
    if call_id is not None:
        stmt = stmt.where(JobError.call_id == call_id)
    rows = await session.scalars(stmt)
    return [
        {
            "id": str(e.id),
            "call_id": str(e.call_id),
            "stage": e.stage,
            "attempt": e.attempt,
            "fatal": e.fatal,
            "error_class": e.error_class,
            "message": e.message,
            "traceback": e.traceback,
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]


@router.get("/admin/lifecycle")
async def lifecycle_list(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    portfolio: uuid.UUID | None = None,
    limit: int = Query(60, ge=1, le=300),
) -> list[dict[str, Any]]:
    """Recent calls with their pipeline state — the super-admin debug feed (newest first)."""
    stmt = (
        select(
            Call.id, Call.batch_id, Call.option, Call.created_at, Agent.name,
            Job.state, Job.attempts, Report.created_at,
        )
        .join(Job, Job.call_id == Call.id, isouter=True)
        .join(Report, Report.call_id == Call.id, isouter=True)
        .join(Agent, Agent.id == Call.agent_id, isouter=True)
        .order_by(Call.created_at.desc())
        .limit(limit)
    )
    if portfolio is not None:
        stmt = stmt.where(Call.portfolio_id == portfolio)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "call_id": str(cid), "batch_id": str(bid) if bid else None, "option": option,
            "folder": folder, "state": state, "attempts": attempts or 0,
            "uploaded_at": created.isoformat() if created else None,
            "report_at": rep_at.isoformat() if rep_at else None,
        }
        for cid, bid, option, created, folder, state, attempts, rep_at in rows
    ]


@router.get("/admin/lifecycle/{call_id}")
async def lifecycle_detail(
    call_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """The full analysis lifecycle of one call: STT → which agents ran → report, + tracebacks."""
    from fastapi import HTTPException, status

    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")
    job = await session.scalar(select(Job).where(Job.call_id == call_id))
    report = await session.scalar(select(Report).where(Report.call_id == call_id))
    folder = await session.get(Agent, call.agent_id)
    errors = list(
        await session.scalars(
            select(JobError).where(JobError.call_id == call_id).order_by(JobError.created_at)
        )
    )
    option = call.option or "FULL"
    agents = _AGENTS_BY_OPTION.get(option, [])

    steps: list[dict[str, Any]] = [
        {"step": "Uploaded", "ok": True, "at": call.created_at.isoformat(),
         "detail": "recording registered + job enqueued"},
        {"step": "STT submitted", "ok": bool(job and job.transcript_id),
         "detail": (job.transcript_id if job and job.transcript_id else "not yet")},
        {"step": "Transcript stored", "ok": bool(call.r2_transcript_uri),
         "detail": "raw transcript in R2" if call.r2_transcript_uri else "pending"},
    ]
    if option == "RAW_ONLY":
        steps.append({"step": "Analysis", "ok": True, "detail": "skipped (raw transcript only)"})
    else:
        steps.append({
            "step": "Analysis", "ok": report is not None,
            "detail": "agents → " + (" → ".join(agents) if agents else "—"),
        })
    steps.append({
        "step": "Report generated", "ok": report is not None,
        "at": report.created_at.isoformat() if report else None,
        "detail": f"option {option}" + (
            f" · flagged: {report.flag_reason}" if report and report.flagged_for_review else ""
        ),
    })

    return {
        "call_id": str(call_id),
        "portfolio_id": str(call.portfolio_id),
        "batch_id": str(call.batch_id) if call.batch_id else None,
        "folder": folder.name if folder else None,
        "agent_name": report.agent_name if report else None,
        "option": option,
        "state": job.state if job else None,
        "attempts": job.attempts if job else 0,
        "last_error": job.last_error if job else None,
        "agents": agents,
        "steps": steps,
        "errors": [
            {
                "stage": e.stage, "attempt": e.attempt, "fatal": e.fatal,
                "error_class": e.error_class, "message": e.message, "traceback": e.traceback,
                "at": e.created_at.isoformat(),
            }
            for e in errors
        ],
    }


@router.get("/admin/activity")
async def activity(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Short who-did-what feed (super admin only): every add/modify/delete action."""
    rows = (
        await session.execute(
            select(AuditLog, User.email)
            .join(User, User.id == AuditLog.actor_id, isouter=True)
            .order_by(AuditLog.ts.desc())
            .limit(limit)
        )
    ).all()
    return [
        {"actor": email or "system", "action": _describe(a.action, a.meta)}
        for a, email in rows
    ]
