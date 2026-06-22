"""Agent CRUD within a portfolio (FR2). RBAC-gated."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.authz import AuthContext, authorize
from app.config import settings
from app.db import get_session
from app.models import Agent, Call
from app.pagination import Page, set_page_headers
from app.rbac import Action
from app.schemas import AgentCreate, AgentOut, AgentUpdate
from app.storage import build_storage

router = APIRouter(tags=["agents"])


async def _require_agent(session: AsyncSession, pid: uuid.UUID, aid: uuid.UUID) -> Agent:
    agent = await session.scalar(select(Agent).where(Agent.id == aid, Agent.portfolio_id == pid))
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    return agent


@router.post(
    "/portfolios/{pid}/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED
)
async def create_agent(
    pid: uuid.UUID,
    body: AgentCreate,
    ctx: Annotated[AuthContext, Depends(authorize(Action.AGENT_CREATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Agent:
    agent = Agent(portfolio_id=pid, name=body.name, external_ref=body.external_ref)
    session.add(agent)
    await session.flush()
    await record_audit(
        session, actor_id=ctx.user.id, action="agent.create",
        entity="agent", entity_id=agent.id, meta={"portfolio_id": str(pid)},
    )
    await session.commit()
    return agent


@router.get("/portfolios/{pid}/agents", response_model=list[AgentOut])
async def list_agents(
    pid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.AGENT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
    page: Annotated[Page, Depends(Page.params)],
) -> list[Agent]:
    base = select(Agent).where(Agent.portfolio_id == pid)
    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    )
    rows = await session.scalars(
        base.order_by(Agent.created_at).limit(page.limit).offset(page.offset)
    )
    set_page_headers(response, total or 0, page)
    return list(rows)


@router.patch("/portfolios/{pid}/agents/{aid}", response_model=AgentOut)
async def rename_agent(
    pid: uuid.UUID,
    aid: uuid.UUID,
    body: AgentUpdate,
    ctx: Annotated[AuthContext, Depends(authorize(Action.AGENT_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Agent:
    agent = await _require_agent(session, pid, aid)
    agent.name = body.name
    await record_audit(
        session, actor_id=ctx.user.id, action="agent.rename",
        entity="agent", entity_id=aid, meta={"name": body.name},
    )
    await session.commit()
    return agent


@router.delete("/portfolios/{pid}/agents/{aid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    pid: uuid.UUID,
    aid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.AGENT_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Remove an agent and all its calls. Purges each call's R2 artifacts first, then the
    DB cascade (agent → calls → jobs, reports, items) removes the rows."""
    agent = await _require_agent(session, pid, aid)
    storage = build_storage(settings)
    calls = (await session.scalars(select(Call).where(Call.agent_id == aid))).all()
    for c in calls:
        await storage.delete_recording(c.r2_audio_uri)
        await storage.delete_transcript(c.id)
        await storage.delete_report(c.id)
    await session.delete(agent)
    await record_audit(
        session, actor_id=ctx.user.id, action="agent.delete",
        entity="agent", entity_id=aid, meta={"calls": len(calls)},
    )
    await session.commit()
