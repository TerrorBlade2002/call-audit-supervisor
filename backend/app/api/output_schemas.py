"""Output schema builder (Structured Outputs §B2 / phase 2) — super-admin CRUD over custom
response schemas per (portfolio, folder, stage).

Org-ADMIN only. A schema is validated on upload: it must be within the model's supported JSON-schema
subset AND contain the stage's operational core (verdicts/objections/feedback). Otherwise it's
rejected (422) with the offending path. At most one in_use per (portfolio, folder, stage).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.authz import AuthContext, authorize_org
from app.db import get_session
from app.judge.schema_store import STAGES
from app.judge.schema_validate import DEFAULT_SCHEMAS, SchemaError, validate_output_schema
from app.models import OutputSchema
from app.rbac import Action
from app.schemas import OutputSchemaCreate, OutputSchemaOut

router = APIRouter(tags=["output-schemas"])


def _check_stage(stage: str) -> None:
    if stage not in STAGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown stage '{stage}' (expected one of {', '.join(STAGES)})",
        )


async def _get(session: AsyncSession, sid: uuid.UUID) -> OutputSchema:
    s = await session.get(OutputSchema, sid)
    if s is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schema not found")
    return s


@router.get("/admin/output-schemas/defaults")
async def output_schema_defaults(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
) -> dict[str, dict[str, Any]]:
    """The built-in default schema per stage — the Load-default starting point + the contract."""
    return dict(DEFAULT_SCHEMAS)


@router.get("/admin/output-schemas", response_model=list[OutputSchemaOut])
async def list_output_schemas(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: str | None = None,
    portfolio_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> list[OutputSchema]:
    """Schemas at the EXACT scope (portfolio_id, agent_id) — both null = the global tier."""
    stmt = (
        select(OutputSchema)
        .where(
            OutputSchema.portfolio_id.is_not_distinct_from(portfolio_id),
            OutputSchema.agent_id.is_not_distinct_from(agent_id),
        )
        .order_by(OutputSchema.agent, OutputSchema.created_at.desc())
    )
    if agent:
        stmt = stmt.where(OutputSchema.agent == agent)
    return list(await session.scalars(stmt))


@router.post("/admin/output-schemas", response_model=OutputSchemaOut, status_code=201)
async def create_output_schema(
    body: OutputSchemaCreate,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OutputSchema:
    _check_stage(body.agent)
    try:
        validate_output_schema(body.agent, body.content)
    except SchemaError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    s = OutputSchema(
        agent=body.agent, portfolio_id=body.portfolio_id, agent_id=body.agent_id,
        name=body.name, content=body.content, created_by=ctx.user.id,
    )
    session.add(s)
    await session.flush()
    await record_audit(
        session, actor_id=ctx.user.id, action="output_schema.create",
        entity="output_schema", entity_id=s.id, meta={"agent": body.agent, "name": body.name},
    )
    await session.commit()
    return s


@router.post("/admin/output-schemas/{sid}/activate", response_model=OutputSchemaOut)
async def activate_output_schema(
    sid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OutputSchema:
    """Make this the in-use schema for its (portfolio, folder, stage) scope (deactivate-first)."""
    s = await _get(session, sid)
    await session.execute(
        update(OutputSchema)
        .where(
            OutputSchema.agent == s.agent,
            OutputSchema.portfolio_id.is_not_distinct_from(s.portfolio_id),
            OutputSchema.agent_id.is_not_distinct_from(s.agent_id),
        )
        .values(in_use=False)
    )
    s.in_use = True
    await record_audit(
        session, actor_id=ctx.user.id, action="output_schema.activate",
        entity="output_schema", entity_id=s.id, meta={"agent": s.agent, "name": s.name},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another schema was activated for this scope at the same time — please retry",
        ) from exc
    await session.refresh(s)
    return s


@router.post("/admin/output-schemas/{sid}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_output_schema(
    sid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Turn this schema OFF (flip to not-in-use). With nothing else in use at the scope, the
    stage reverts to its built-in Pydantic schema — i.e. this is the "use the default" action."""
    s = await _get(session, sid)
    s.in_use = False
    await record_audit(
        session, actor_id=ctx.user.id, action="output_schema.deactivate",
        entity="output_schema", entity_id=s.id, meta={"agent": s.agent, "name": s.name},
    )
    await session.commit()


@router.delete("/admin/output-schemas/{sid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_output_schema(
    sid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete a schema. If it was in use, the stage falls back to its built-in Pydantic schema."""
    s = await _get(session, sid)
    await session.delete(s)
    await record_audit(
        session, actor_id=ctx.user.id, action="output_schema.delete",
        entity="output_schema", entity_id=sid, meta={"agent": s.agent, "name": s.name},
    )
    await session.commit()
