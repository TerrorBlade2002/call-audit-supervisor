"""Portfolio CRUD + membership assignment (Phase 0). All routes RBAC-gated (§2)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.authz import AuthContext, authorize, authorize_org
from app.checklists.service import create_default_checklist
from app.db import get_session
from app.kb.seed import seed_default_kb
from app.models import OrgMember, Portfolio, PortfolioMember, User
from app.models import Role as RoleRow
from app.pagination import Page, set_page_headers
from app.passwords import hash_password
from app.rbac import Action, Role
from app.schemas import (
    MemberAssign,
    PortfolioCreate,
    PortfolioOut,
    PortfolioUserCreate,
    PortfolioUserOut,
)
from app.security import CurrentUser

router = APIRouter(tags=["portfolios"])


@router.post("/portfolios", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    body: PortfolioCreate,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.PORTFOLIO_CREATE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Portfolio:
    portfolio = Portfolio(name=body.name, created_by=ctx.user.id)
    session.add(portfolio)
    await session.flush()
    # Ship the default checklist + the Everest KB with every new portfolio (FR6.1/FR5).
    await create_default_checklist(session, portfolio.id)
    await seed_default_kb(session, portfolio.id)
    await record_audit(
        session, actor_id=ctx.user.id, action="portfolio.create",
        entity="portfolio", entity_id=portfolio.id, meta={"name": body.name},
    )
    await session.commit()
    return portfolio


@router.get("/portfolios", response_model=list[PortfolioOut])
async def list_portfolios(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
    page: Annotated[Page, Depends(Page.params)],
) -> list[PortfolioOut]:
    """Scoped list: org ADMIN sees all; others see portfolios they're members of (FR1)."""
    is_admin = await session.scalar(
        select(OrgMember)
        .join(RoleRow, RoleRow.id == OrgMember.role_id)
        .where(OrgMember.user_id == user.id, RoleRow.name == Role.ADMIN)
    )
    if is_admin is not None:
        response.headers["X-Is-Org-Admin"] = "true"
        base = select(Portfolio)
        total = await session.scalar(select(func.count()).select_from(base.subquery()))
        rows = list(
            await session.scalars(
                base.order_by(Portfolio.created_at).limit(page.limit).offset(page.offset)
            )
        )
        set_page_headers(response, total or 0, page)
        return [_with_role(p, "ADMIN") for p in rows]

    # Non-admin: only portfolios they're a member of, annotated with their role there.
    member_q = (
        select(Portfolio, RoleRow.name)
        .join(PortfolioMember, PortfolioMember.portfolio_id == Portfolio.id)
        .join(RoleRow, RoleRow.id == PortfolioMember.role_id)
        .where(PortfolioMember.user_id == user.id)
    )
    total = await session.scalar(
        select(func.count()).select_from(member_q.subquery())
    )
    result = (
        await session.execute(
            member_q.order_by(Portfolio.created_at).limit(page.limit).offset(page.offset)
        )
    ).all()
    set_page_headers(response, total or 0, page)
    return [_with_role(p, role) for p, role in result]


def _with_role(portfolio: Portfolio, role: str | None) -> PortfolioOut:
    return PortfolioOut(
        id=portfolio.id, name=portfolio.name, created_at=portfolio.created_at, my_role=role
    )


@router.patch("/portfolios/{pid}", response_model=PortfolioOut)
async def rename_portfolio(
    pid: uuid.UUID,
    body: PortfolioCreate,
    ctx: Annotated[AuthContext, Depends(authorize(Action.PORTFOLIO_DELETE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PortfolioOut:
    """Rename a portfolio (super admin only — same gate as delete). Saved immediately."""
    portfolio = await session.get(Portfolio, pid)
    if portfolio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="portfolio not found")
    portfolio.name = body.name
    await record_audit(
        session, actor_id=ctx.user.id, action="portfolio.rename",
        entity="portfolio", entity_id=pid, meta={"name": body.name},
    )
    await session.commit()
    return _with_role(portfolio, "ADMIN")


@router.delete("/portfolios/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_portfolio(
    pid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.PORTFOLIO_DELETE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await session.execute(delete(Portfolio).where(Portfolio.id == pid))
    await record_audit(
        session, actor_id=ctx.user.id, action="portfolio.delete",
        entity="portfolio", entity_id=pid,
    )
    await session.commit()


@router.post("/portfolios/{pid}/members", status_code=status.HTTP_204_NO_CONTENT)
async def assign_member(
    pid: uuid.UUID,
    body: MemberAssign,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Assign a user a portfolio-scoped role (ADMIN only, §2)."""
    role_id = await session.scalar(select(RoleRow.id).where(RoleRow.name == body.role))
    if role_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown role")

    existing = await session.get(PortfolioMember, {"user_id": body.user_id, "portfolio_id": pid})
    if existing is None:
        session.add(
            PortfolioMember(user_id=body.user_id, portfolio_id=pid, role_id=role_id)
        )
    else:
        existing.role_id = role_id
    await record_audit(
        session, actor_id=ctx.user.id, action="portfolio.member.assign",
        entity="portfolio_member", entity_id=pid,
        meta={"user_id": str(body.user_id), "role": body.role},
    )
    await session.commit()


# ── Per-portfolio user accounts (super admin only): create/list/delete supervisors & agents ──


@router.post(
    "/portfolios/{pid}/users", response_model=PortfolioUserOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_portfolio_user(
    pid: uuid.UUID,
    body: PortfolioUserCreate,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PortfolioUserOut:
    """Create (or re-credential) a SUPERVISOR/AGENT and assign them to this portfolio."""
    if body.role not in (Role.SUPERVISOR, Role.AGENT):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be SUPERVISOR or AGENT",
        )
    role_id = await session.scalar(select(RoleRow.id).where(RoleRow.name == body.role))
    if role_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown role")
    email = body.email.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email, name=email.split("@")[0], status="active",
            password_hash=hash_password(body.password),
        )
        session.add(user)
        await session.flush()
    else:
        user.password_hash = hash_password(body.password)  # reset the password
    existing = await session.get(
        PortfolioMember, {"user_id": user.id, "portfolio_id": pid}
    )
    if existing is None:
        session.add(PortfolioMember(user_id=user.id, portfolio_id=pid, role_id=role_id))
    else:
        existing.role_id = role_id
    await record_audit(
        session, actor_id=ctx.user.id, action="portfolio.user.create",
        entity="portfolio_user", entity_id=pid, meta={"email": email, "role": body.role},
    )
    await session.commit()
    return PortfolioUserOut(id=user.id, email=user.email, name=user.name, role=body.role)


@router.get("/portfolios/{pid}/users", response_model=list[PortfolioUserOut])
async def list_portfolio_users(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PortfolioUserOut]:
    rows = (
        await session.execute(
            select(User.id, User.email, User.name, RoleRow.name)
            .join(PortfolioMember, PortfolioMember.user_id == User.id)
            .join(RoleRow, RoleRow.id == PortfolioMember.role_id)
            .where(PortfolioMember.portfolio_id == pid)
            .order_by(User.email)
        )
    ).all()
    return [PortfolioUserOut(id=i, email=e, name=n, role=r) for i, e, n, r in rows]


@router.delete(
    "/portfolios/{pid}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_portfolio_user(
    pid: uuid.UUID,
    user_id: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_org(Action.USER_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Remove a user's access to this portfolio (deletes the membership, keeps the account)."""
    await session.execute(
        delete(PortfolioMember).where(
            PortfolioMember.user_id == user_id, PortfolioMember.portfolio_id == pid
        )
    )
    await record_audit(
        session, actor_id=ctx.user.id, action="portfolio.user.delete",
        entity="portfolio_user", entity_id=pid, meta={"user_id": str(user_id)},
    )
    await session.commit()
