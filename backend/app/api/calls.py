"""Register uploaded recordings → create calls + enqueue jobs (FR3.1/FR3.4).

This is the seam between ingestion (Phase 1) and the orchestration engine (Phase 2): each
registered object becomes a ``call`` row + a ``job`` in PENDING_TRANSCRIPTION that the
worker then picks up. One job per file keeps retry/idempotency at per-file granularity.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.authz import AuthContext, authorize
from app.config import settings
from app.db import get_session
from app.judge.options import ProcessingOption
from app.models import Agent, Call, Job, JobState, Report
from app.notifier import PgNotifier
from app.orchestration import queue
from app.pagination import Page, set_page_headers
from app.rbac import Action
from app.schemas import CallOut, CallRegisterRequest, CallRegisterResponse, UploadQuotaOut
from app.storage import build_storage

router = APIRouter(tags=["calls"])
_notifier = PgNotifier()

# Per-portfolio in-flight cap (NFR3): at most this many recordings may be processing at once
# across the whole portfolio. A recording is "in-flight" until its job reaches a terminal state.
MAX_INFLIGHT_PER_PORTFOLIO = 10
_TERMINAL_STATES = (JobState.DONE.value, JobState.FAILED.value)


async def _inflight_count(session: AsyncSession, pid: uuid.UUID) -> int:
    """How many recordings in this portfolio are still processing (job not DONE/FAILED)."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.portfolio_id == pid, Job.state.notin_(_TERMINAL_STATES))
        )
    ) or 0


async def assert_inflight_quota(session: AsyncSession, pid: uuid.UUID, incoming: int) -> None:
    """Reject (429) if uploading ``incoming`` recordings would exceed the portfolio's in-flight cap.

    Remaining quota = MAX − (recordings still processing). Once the current batch finishes the
    quota is the full MAX again. Checked before any bytes are written, so nothing is uploaded
    when over quota.
    """
    in_flight = await _inflight_count(session, pid)
    remaining = MAX_INFLIGHT_PER_PORTFOLIO - in_flight
    if incoming > remaining:
        if remaining <= 0:
            detail = (
                f"This portfolio already has {in_flight} recording(s) processing "
                f"(max {MAX_INFLIGHT_PER_PORTFOLIO} at a time). Wait for the current batch to "
                "finish before uploading more."
            )
        else:
            detail = (
                f"You can upload at most {remaining} more recording(s) right now — {in_flight} "
                f"are still processing in this portfolio (max {MAX_INFLIGHT_PER_PORTFOLIO} at a "
                "time). Wait for the current batch to finish to free up the rest."
            )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)


async def _require_agent(session: AsyncSession, pid: uuid.UUID, aid: uuid.UUID) -> None:
    agent = await session.scalar(select(Agent).where(Agent.id == aid, Agent.portfolio_id == pid))
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")


@router.post(
    "/portfolios/{pid}/agents/{aid}/calls",
    response_model=CallRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_calls(
    pid: uuid.UUID,
    aid: uuid.UUID,
    body: CallRegisterRequest,
    ctx: Annotated[AuthContext, Depends(authorize(Action.RECORDING_UPLOAD))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CallRegisterResponse:
    await _require_agent(session, pid, aid)

    # Keys must be within this agent's prefix (issued by the presign step). Guards against
    # registering arbitrary objects.
    prefix = f"{pid}/{aid}/"
    for item in body.items:
        if not item.key.startswith(prefix):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"key outside agent scope: {item.key}",
            )
    await assert_inflight_quota(session, pid, len(body.items))

    return await register_keys(
        session,
        pid=pid,
        aid=aid,
        keys=[(item.key, item.duration_sec) for item in body.items],
        uploaded_by=ctx.user.id,
    )


@router.get("/portfolios/{pid}/upload-quota", response_model=UploadQuotaOut)
async def upload_quota(
    pid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.RECORDING_UPLOAD))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UploadQuotaOut:
    """The portfolio's current upload headroom — how many more recordings can be queued now."""
    in_flight = await _inflight_count(session, pid)
    return UploadQuotaOut(
        max=MAX_INFLIGHT_PER_PORTFOLIO,
        in_flight=in_flight,
        remaining=max(0, MAX_INFLIGHT_PER_PORTFOLIO - in_flight),
    )


async def register_keys(
    session: AsyncSession,
    *,
    pid: uuid.UUID,
    aid: uuid.UUID,
    keys: list[tuple[str, int | None]],
    uploaded_by: uuid.UUID,
    option: ProcessingOption = ProcessingOption.FULL,
    checklist_id: uuid.UUID | None = None,
    kb_doc_ids: list[uuid.UUID] | None = None,
) -> CallRegisterResponse:
    """Create a call + PENDING_TRANSCRIPTION job per uploaded key, then commit.

    Shared by ``register_calls`` (presigned-direct keys) and the upload-proxy endpoint
    (server-uploaded keys), so both paths enqueue identically. The whole batch carries one
    processing OPTION + the checklist/KB selection that the pipeline reads per call (§7.3).
    """
    batch_id = uuid.uuid4()
    kb_ids = [str(x) for x in kb_doc_ids] if kb_doc_ids else None
    created_ids: list[uuid.UUID] = []
    for key, duration in keys:
        call = Call(
            agent_id=aid,
            portfolio_id=pid,
            r2_audio_uri=key,
            duration_sec=duration,
            batch_id=batch_id,
            uploaded_by=uploaded_by,
            option=option.value,
            checklist_id=checklist_id,
            kb_doc_ids=kb_ids,
        )
        session.add(call)
        await session.flush()
        await queue.enqueue(
            session,
            call_id=call.id,
            portfolio_id=pid,
            max_attempts=settings.ratelimit.retry_max_attempts,
        )
        created_ids.append(call.id)
        # "uploaded/queued" event (FR3.3/FR13). Worker emits the later transitions.
        await _notifier.publish(
            session, portfolio_id=pid, call_id=call.id, state="PENDING_TRANSCRIPTION"
        )

    await record_audit(
        session, actor_id=uploaded_by, action="calls.register",
        entity="batch", entity_id=batch_id, meta={"count": len(created_ids), "agent_id": str(aid)},
    )
    await session.commit()

    calls = await _load_calls(session, created_ids)
    return CallRegisterResponse(batch_id=batch_id, calls=calls)


@router.get("/portfolios/{pid}/agents/{aid}/calls", response_model=list[CallOut])
async def list_calls(
    pid: uuid.UUID,
    aid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.AGENT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
    page: Annotated[Page, Depends(Page.params)],
) -> list[CallOut]:
    total = await session.scalar(
        select(func.count())
        .select_from(Call)
        .where(Call.agent_id == aid, Call.portfolio_id == pid)
    )
    rows = (
        await session.execute(
            select(Call, Job.state, Job.last_error, Report.id, Report.created_at, Job.updated_at)
            .join(Job, Job.call_id == Call.id, isouter=True)
            .join(Report, Report.call_id == Call.id, isouter=True)
            .where(Call.agent_id == aid, Call.portfolio_id == pid)
            .order_by(Call.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    set_page_headers(response, total or 0, page)
    return [_to_call_out(*row) for row in rows]


@router.delete(
    "/portfolios/{pid}/agents/{aid}/calls/{call_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_call(
    pid: uuid.UUID,
    aid: uuid.UUID,
    call_id: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.RECORDING_DELETE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Remove a call: purge its recording + transcript from R2, then delete the row.

    The DB cascade (calls → job, report, report_items, verifications, objections) cleans
    up the rest. R2 deletes are best-effort + idempotent so a missing object never blocks
    the row removal.
    """
    call = await session.scalar(
        select(Call).where(Call.id == call_id, Call.agent_id == aid, Call.portfolio_id == pid)
    )
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")

    storage = build_storage(settings)
    await storage.delete_recording(call.r2_audio_uri)
    await storage.delete_transcript(call_id)
    await storage.delete_report(call_id)

    await session.delete(call)
    await record_audit(
        session, actor_id=ctx.user.id, action="call.delete",
        entity="call", entity_id=call_id, meta={"agent_id": str(aid)},
    )
    await session.commit()


async def _load_calls(session: AsyncSession, ids: list[uuid.UUID]) -> list[CallOut]:
    rows = (
        await session.execute(
            select(Call, Job.state, Job.last_error, Report.id, Report.created_at, Job.updated_at)
            .join(Job, Job.call_id == Call.id, isouter=True)
            .join(Report, Report.call_id == Call.id, isouter=True)
            .where(Call.id.in_(ids))
            .order_by(Call.created_at)
        )
    ).all()
    return [_to_call_out(*row) for row in rows]


def _clean_error(raw: str | None) -> str | None:
    """Strip the engine's internal prefixes so the UI shows just the human reason."""
    if not raw:
        return None
    for prefix in ("fatal: ", "max attempts (", "RetryableError: "):
        if raw.startswith(prefix):
            # "max attempts (5) exhausted: <reason>" → keep the reason after the colon.
            return raw.split(": ", 1)[1] if ": " in raw else raw
    return raw


def _to_call_out(
    call: Call,
    state: str | None,
    last_error: str | None,
    report_id: uuid.UUID | None,
    report_at: datetime | None = None,
    job_updated_at: datetime | None = None,
) -> CallOut:
    # "Completed" time: when the report landed (DONE), or when the job last changed if FAILED.
    completed_at = report_at if report_at is not None else (
        job_updated_at if state == "FAILED" else None
    )
    return CallOut(
        id=call.id,
        agent_id=call.agent_id,
        duration_sec=call.duration_sec,
        batch_id=call.batch_id,
        status=state,
        last_error=_clean_error(last_error) if state == "FAILED" else None,
        report_id=report_id,
        option=call.option,
        created_at=call.created_at,
        completed_at=completed_at,
    )
