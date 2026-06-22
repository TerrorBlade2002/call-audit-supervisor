"""Prompt builder (§B) — super-admin CRUD over agent prompt bodies, with safe activation.

Org-ADMIN only. Each agent (feedback/checklist/ideal/merged) can have many saved prompts but at
most one "in use" at a time (enforced by a partial unique index). Activation is atomic: deactivate
the agent's prompts, then activate the chosen one — concurrent activations are caught and 409'd.
Agents read the in-use body at judge time and fall back to the hard-coded default when none.
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
from app.judge.prompt_store import AGENTS, DEFAULT_BODY
from app.models import AgentPrompt
from app.rbac import Action
from app.schemas import AgentPromptCreate, AgentPromptOut

router = APIRouter(tags=["prompts"])


def _check_agent(agent: str) -> None:
    if agent not in AGENTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown agent '{agent}' (expected one of {', '.join(AGENTS)})",
        )


async def _get(session: AsyncSession, pid: uuid.UUID) -> AgentPrompt:
    p = await session.get(AgentPrompt, pid)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="prompt not found")
    return p


@router.get("/admin/prompts/defaults")
async def prompt_defaults(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
) -> dict[str, str]:
    """The built-in default body per agent — shown when no custom prompt is in use / as a base."""
    return dict(DEFAULT_BODY)


@router.get("/admin/prompts", response_model=list[AgentPromptOut])
async def list_prompts(
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: str | None = None,
    portfolio_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> list[AgentPrompt]:
    """Prompts at the EXACT scope (portfolio_id, agent_id) — both null = the global tier."""
    stmt = (
        select(AgentPrompt)
        .where(
            AgentPrompt.portfolio_id.is_not_distinct_from(portfolio_id),
            AgentPrompt.agent_id.is_not_distinct_from(agent_id),
        )
        .order_by(AgentPrompt.agent, AgentPrompt.created_at.desc())
    )
    if agent:
        stmt = stmt.where(AgentPrompt.agent == agent)
    return list(await session.scalars(stmt))


@router.post("/admin/prompts", response_model=AgentPromptOut, status_code=201)
async def create_prompt(
    body: AgentPromptCreate,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentPrompt:
    _check_agent(body.agent)
    p = AgentPrompt(
        agent=body.agent, portfolio_id=body.portfolio_id, agent_id=body.agent_id,
        name=body.name, content=body.content, created_by=ctx.user.id,
    )
    session.add(p)
    await session.flush()
    await record_audit(
        session, actor_id=ctx.user.id, action="prompt.create",
        entity="agent_prompt", entity_id=p.id, meta={"agent": body.agent, "name": body.name},
    )
    await session.commit()
    return p


@router.post("/admin/prompts/upload", response_model=AgentPromptOut, status_code=201)
async def upload_prompt(
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[str, Form()],
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File(...)],
) -> AgentPrompt:
    """Create a prompt from an uploaded .md file (the body is the file's markdown)."""
    _check_agent(agent)
    if not (file.filename or "").lower().endswith((".md", ".markdown", ".txt")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="upload a .md (or .txt) file"
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
    p = AgentPrompt(agent=agent, name=name, content=content, created_by=ctx.user.id)
    session.add(p)
    await session.flush()
    await record_audit(
        session, actor_id=ctx.user.id, action="prompt.create",
        entity="agent_prompt", entity_id=p.id, meta={"agent": agent, "name": name},
    )
    await session.commit()
    return p


@router.post("/admin/prompts/{pid}/activate", response_model=AgentPromptOut)
async def activate_prompt(
    pid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentPrompt:
    """Make this the in-use prompt for its agent (atomic: deactivate the agent's others first)."""
    p = await _get(session, pid)
    # Deactivate the agent's prompts AT THE SAME SCOPE, then activate the chosen one — so a
    # folder activation never disturbs the portfolio/global tiers. At commit the partial unique
    # index guarantees exactly one in_use per (portfolio, folder, agent); a concurrent activate
    # loses with IntegrityError.
    await session.execute(
        update(AgentPrompt)
        .where(
            AgentPrompt.agent == p.agent,
            AgentPrompt.portfolio_id.is_not_distinct_from(p.portfolio_id),
            AgentPrompt.agent_id.is_not_distinct_from(p.agent_id),
        )
        .values(in_use=False)
    )
    p.in_use = True
    await record_audit(
        session, actor_id=ctx.user.id, action="prompt.activate",
        entity="agent_prompt", entity_id=p.id, meta={"agent": p.agent, "name": p.name},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another prompt was activated for this agent at the same time — please retry",
        ) from exc
    await session.refresh(p)
    return p


@router.delete("/admin/prompts/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(
    pid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete a prompt. If it was in use, the agent falls back to its built-in default."""
    p = await _get(session, pid)
    await session.delete(p)
    await record_audit(
        session, actor_id=ctx.user.id, action="prompt.delete",
        entity="agent_prompt", entity_id=pid, meta={"agent": p.agent, "name": p.name},
    )
    await session.commit()
