"""Report template builder (schema-hardening §B2) — super-admin CRUD over HTML report layouts.

Org-ADMIN only. A template is bound per (portfolio, folder) with at most one in_use per scope
(partial unique index). At judge/report time the most-specific in_use template renders the report
deterministically (logic-less population — no LLM in the layout). Uploads are VALIDATED against
the report data dictionary: a template that references a non-existent field is rejected (422) with
the offending path, so a template and its data can never silently drift.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.authz import AuthContext, authorize_org
from app.db import get_session
from app.models import ReportTemplate
from app.rbac import Action
from app.reports.template import DATA_FIELDS, TemplateError, validate_template
from app.schemas import ReportTemplateCreate, ReportTemplateOut

router = APIRouter(tags=["report-templates"])


def _validate(content: str) -> None:
    try:
        validate_template(content)
    except TemplateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


async def _get(session: AsyncSession, tid: uuid.UUID) -> ReportTemplate:
    t = await session.get(ReportTemplate, tid)
    if t is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    return t


@router.get("/admin/report-templates/fields")
async def template_fields(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
) -> dict[str, object]:
    """The report data dictionary — the fields a template may reference (a palette for the UI)."""
    def describe(node: object) -> object:
        if isinstance(node, tuple) and node and node[0] == "list":
            inner = node[1]
            return {"type": "list", "of": describe(inner) if isinstance(inner, dict) else inner}
        if isinstance(node, dict):
            return {k: describe(v) for k, v in node.items()}
        return node
    return {k: describe(v) for k, v in DATA_FIELDS.items()}


@router.get("/admin/report-templates", response_model=list[ReportTemplateOut])
async def list_templates(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    portfolio_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> list[ReportTemplate]:
    """Templates at the EXACT scope (portfolio_id, agent_id) — both null = the global tier."""
    stmt = (
        select(ReportTemplate)
        .where(
            ReportTemplate.portfolio_id.is_not_distinct_from(portfolio_id),
            ReportTemplate.agent_id.is_not_distinct_from(agent_id),
        )
        .order_by(ReportTemplate.created_at.desc())
    )
    return list(await session.scalars(stmt))


@router.post("/admin/report-templates", response_model=ReportTemplateOut, status_code=201)
async def create_template(
    body: ReportTemplateCreate,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReportTemplate:
    _validate(body.content)
    t = ReportTemplate(
        portfolio_id=body.portfolio_id, agent_id=body.agent_id,
        name=body.name, content=body.content, created_by=ctx.user.id,
    )
    session.add(t)
    await session.flush()
    await record_audit(
        session, actor_id=ctx.user.id, action="report_template.create",
        entity="report_template", entity_id=t.id, meta={"name": body.name},
    )
    await session.commit()
    return t


@router.post("/admin/report-templates/upload", response_model=ReportTemplateOut, status_code=201)
async def upload_template(
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File(...)],
    portfolio_id: Annotated[uuid.UUID | None, Form()] = None,
    agent_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ReportTemplate:
    """Create a template from an uploaded .html file (validated against the data dictionary)."""
    if not (file.filename or "").lower().endswith((".html", ".htm", ".mustache", ".txt")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="upload an .html template"
        )
    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="file is not valid UTF-8 text"
        ) from exc
    if not content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="the file is empty"
        )
    _validate(content)
    t = ReportTemplate(
        portfolio_id=portfolio_id, agent_id=agent_id, name=name, content=content,
        created_by=ctx.user.id,
    )
    session.add(t)
    await session.flush()
    await record_audit(
        session, actor_id=ctx.user.id, action="report_template.create",
        entity="report_template", entity_id=t.id, meta={"name": name},
    )
    await session.commit()
    return t


@router.post("/admin/report-templates/{tid}/activate", response_model=ReportTemplateOut)
async def activate_template(
    tid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReportTemplate:
    """Make this the in-use template for its (portfolio, folder) scope (atomic deactivate-first)."""
    t = await _get(session, tid)
    await session.execute(
        update(ReportTemplate)
        .where(
            ReportTemplate.portfolio_id.is_not_distinct_from(t.portfolio_id),
            ReportTemplate.agent_id.is_not_distinct_from(t.agent_id),
        )
        .values(in_use=False)
    )
    t.in_use = True
    await record_audit(
        session, actor_id=ctx.user.id, action="report_template.activate",
        entity="report_template", entity_id=t.id, meta={"name": t.name},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another template was activated for this scope at the same time — please retry",
        ) from exc
    await session.refresh(t)
    return t


@router.delete("/admin/report-templates/{tid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    tid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete a template. If it was in use, the scope falls back to the built-in renderer."""
    t = await _get(session, tid)
    await session.delete(t)
    await record_audit(
        session, actor_id=ctx.user.id, action="report_template.delete",
        entity="report_template", entity_id=tid, meta={"name": t.name},
    )
    await session.commit()
