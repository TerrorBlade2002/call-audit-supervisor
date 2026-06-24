"""Checklist builder API (FR6).

Reads need CHECKLIST_VIEW; edits need CHECKLIST_MANAGE. Editing a checklist already used by
a report transparently creates a new version (see service.update_checklist). A checklist can be
authored in the editor or uploaded as a .txt in the Everest format (parsed by /parse).
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.authz import AuthContext, authorize
from app.checklists import is_free_text, service
from app.checklists.parse import ParseError, parse_checklist
from app.db import get_session
from app.models import Agent, Call, Checklist, ChecklistItem, Report, ReportItem
from app.rbac import Action
from app.schemas import (
    ChecklistCreate,
    ChecklistDetailOut,
    ChecklistItemIn,
    ChecklistItemOut,
    ChecklistOut,
    ChecklistUpdate,
    ParsedChecklistOut,
)

router = APIRouter(tags=["checklists"])


async def _get_checklist(session: AsyncSession, pid: uuid.UUID, cid: uuid.UUID) -> Checklist:
    cl = await session.scalar(
        select(Checklist).where(Checklist.id == cid, Checklist.portfolio_id == pid)
    )
    if cl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="checklist not found")
    return cl


async def _detail(session: AsyncSession, cl: Checklist) -> ChecklistDetailOut:
    items = await service.get_items(session, cl.id)
    return ChecklistDetailOut(
        **ChecklistOut.model_validate(cl).model_dump(),
        items=[ChecklistItemOut.model_validate(i) for i in items],
    )


@router.get("/portfolios/{pid}/checklists", response_model=list[ChecklistOut])
async def list_checklists(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.CHECKLIST_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Checklist]:
    return await service.list_active_checklists(session, pid)


@router.get("/portfolios/{pid}/checklists/{cid}", response_model=ChecklistDetailOut)
async def get_checklist(
    pid: uuid.UUID,
    cid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.CHECKLIST_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChecklistDetailOut:
    cl = await _get_checklist(session, pid, cid)
    return await _detail(session, cl)


@router.post(
    "/portfolios/{pid}/checklists", response_model=ChecklistDetailOut, status_code=201
)
async def create_checklist(
    pid: uuid.UUID,
    body: ChecklistCreate,
    ctx: Annotated[AuthContext, Depends(authorize(Action.CHECKLIST_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChecklistDetailOut:
    cl = await service.create_checklist(
        session, pid, name=body.name, items=[i.model_dump() for i in body.items],
        requires_kb=body.requires_kb,
    )
    await record_audit(
        session, actor_id=ctx.user.id, action="checklist.create",
        entity="checklist", entity_id=cl.id,
    )
    await session.commit()
    return await _detail(session, cl)


@router.put("/portfolios/{pid}/checklists/{cid}", response_model=ChecklistDetailOut)
async def update_checklist(
    pid: uuid.UUID,
    cid: uuid.UUID,
    body: ChecklistUpdate,
    ctx: Annotated[AuthContext, Depends(authorize(Action.CHECKLIST_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChecklistDetailOut:
    cl = await _get_checklist(session, pid, cid)
    updated = await service.update_checklist(
        session, cl, name=body.name, items=[i.model_dump() for i in body.items],
        requires_kb=body.requires_kb,
    )
    await record_audit(
        session, actor_id=ctx.user.id, action="checklist.update",
        entity="checklist", entity_id=updated.id,
        meta={"version": updated.version, "new_version": updated.id != cl.id},
    )
    await session.commit()
    return await _detail(session, updated)


@router.get("/portfolios/{pid}/checklists/{cid}/export.csv")
async def export_checklist_csv(
    pid: uuid.UUID,
    cid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """A merged CSV of every call judged under this checklist (all versions of its family):
    one row per call — call id, agent (from the transcript), upload time, and one column per
    checklist item's verdict. Generated on demand from Postgres; new calls auto-append."""
    cl = await _get_checklist(session, pid, cid)
    family = list(
        await session.scalars(select(Checklist.id).where(Checklist.family_id == cl.family_id))
    )
    columns = [it.text for it in await service.get_items(session, cl.id)]  # ordered by sort_order
    rep_rows = (
        await session.execute(
            select(
                Report.id,
                Report.call_id,
                func.coalesce(Call.agent_name_override, Report.agent_name),
                Call.created_at,
                Agent.name,
            )
            .join(Call, Call.id == Report.call_id)
            .join(Agent, Agent.id == Call.agent_id, isouter=True)
            .where(Report.checklist_id.in_(family))
            .order_by(Call.created_at)
        )
    ).all()
    verdicts = await _load_verdicts(session, [r[0] for r in rep_rows])
    fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{cl.name}_checklist").strip("_") + ".csv"
    return _csv_response(rep_rows, columns, verdicts, fname)


async def _load_verdicts(
    session: AsyncSession, report_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, str]]:
    """Per-report {item text → verdict} for the checklist CSV exports."""
    verdicts: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    if report_ids:
        item_rows = (
            await session.execute(
                select(
                    ReportItem.report_id,
                    ChecklistItem.text,
                    ReportItem.answer,
                    ReportItem.raw_answer,
                    ChecklistItem.answer_type,
                    ChecklistItem.is_subjective,
                )
                .join(ChecklistItem, ChecklistItem.id == ReportItem.checklist_item_id)
                .where(ReportItem.report_id.in_(report_ids))
            )
        ).all()
        for rid, text, ans, raw, atype, subj in item_rows:
            # Free-text (subjective) items have no PASS/FAIL — write their written answer instead.
            if is_free_text(answer_type=atype, is_subjective=subj):
                verdicts[rid][text] = (raw or "").strip() or "—"
            else:
                verdicts[rid][text] = ans or "NA"
    return verdicts


def _csv_response(
    rep_rows: Sequence[Any], columns: list[str], verdicts: dict[uuid.UUID, dict[str, str]],
    fname: str,
) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Call ID", "Agent", "Uploaded", *columns])
    for rid, call_id, agent_name, uploaded, folder in rep_rows:
        name = agent_name or folder or "—"
        writer.writerow(
            [str(call_id), name, uploaded.isoformat(), *[verdicts[rid].get(c, "") for c in columns]]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/portfolios/{pid}/batches/{batch_id}/checklist.csv")
async def export_batch_csv(
    pid: uuid.UUID,
    batch_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Checklist CSV for just one upload batch (the calls in it that produced a checklist).

    Same shape as the per-checklist export, scoped to the batch — the 'download just these
    calls' affordance. 404 when the batch has no checklist results (e.g. a feedback-only batch).
    """
    rep_rows = (
        await session.execute(
            select(
                Report.id,
                Report.call_id,
                func.coalesce(Call.agent_name_override, Report.agent_name),
                Call.created_at,
                Agent.name,
                Report.checklist_id,
            )
            .join(Call, Call.id == Report.call_id)
            .join(Agent, Agent.id == Call.agent_id, isouter=True)
            .where(
                Call.batch_id == batch_id,
                Call.portfolio_id == pid,
                Report.checklist_id.isnot(None),
            )
            .order_by(Call.created_at)
        )
    ).all()
    if not rep_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no checklist results for this batch"
        )
    columns = [it.text for it in await service.get_items(session, rep_rows[0][5])]
    verdicts = await _load_verdicts(session, [r[0] for r in rep_rows])
    rows5 = [(r[0], r[1], r[2], r[3], r[4]) for r in rep_rows]
    return _csv_response(rows5, columns, verdicts, f"batch_{str(batch_id)[:8]}_checklist.csv")


@router.post("/portfolios/{pid}/checklists/parse", response_model=ParsedChecklistOut)
async def parse_checklist_txt(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.CHECKLIST_MANAGE))],
    file: Annotated[UploadFile, File(...)],
) -> ParsedChecklistOut:
    """Parse an uploaded .txt in the Everest checklist format into editable items.

    Does NOT save — returns the parsed name + items for the builder to load, review, and Save.
    A file that doesn't match the format returns 422 so the UI can tell the user to use the
    editor instead. (Manual add only — no LLM call.)
    """
    if not (file.filename or "").lower().endswith(".txt"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please upload a .txt file."
        )
    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content = raw.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Couldn't read the file as text.",
            ) from exc
    try:
        name, items = parse_checklist(content)
    except ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Couldn't parse the checklist — invalid format ({exc}).",
        ) from exc
    return ParsedChecklistOut(
        name=name,
        items=[ChecklistItemIn.model_validate(it, from_attributes=True) for it in items],
    )
