"""Authorization — the single ``(user, action, resource) -> allow/deny`` gate (§2/§57).

One resolver maps a user to their effective ``Role`` in a portfolio (org-wide ADMIN
grants win), then the one ``is_allowed`` matrix decides. Routes depend on ``authorize``
(portfolio-scoped) or ``authorize_org`` (org-scoped, e.g. create portfolio). No route
implements its own permission logic.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import OrgMember, PortfolioMember, User
from app.models import Role as RoleRow
from app.rbac import Action, Role, is_allowed
from app.security import CurrentUser


@dataclass(frozen=True)
class AuthContext:
    """What a passed authorization check yields to the route handler."""

    user: User
    role: Role
    portfolio_id: uuid.UUID | None


async def _org_role(session: AsyncSession, user_id: uuid.UUID) -> Role | None:
    """Org-wide role for a user (today: only ADMIN). None if no org grant."""
    name = await session.scalar(
        select(RoleRow.name)
        .join(OrgMember, OrgMember.role_id == RoleRow.id)
        .where(OrgMember.user_id == user_id)
    )
    return Role(name) if name is not None else None


async def _portfolio_role(
    session: AsyncSession, user_id: uuid.UUID, portfolio_id: uuid.UUID
) -> Role | None:
    """Effective role in a portfolio: org ADMIN wins, else the portfolio-scoped role."""
    org = await _org_role(session, user_id)
    if org is Role.ADMIN:
        return Role.ADMIN
    name = await session.scalar(
        select(RoleRow.name)
        .join(PortfolioMember, PortfolioMember.role_id == RoleRow.id)
        .where(
            PortfolioMember.user_id == user_id,
            PortfolioMember.portfolio_id == portfolio_id,
        )
    )
    if name is not None:
        return Role(name)
    return org  # org grant (if any) even when not a portfolio member


_FORBIDDEN = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


def authorize(action: Action) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency factory for portfolio-scoped routes. Resolves ``{pid}`` from the path."""

    async def dependency(
        pid: uuid.UUID,
        user: CurrentUser,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> AuthContext:
        role = await _portfolio_role(session, user.id, pid)
        if role is None or not is_allowed(role, action):
            raise _FORBIDDEN
        return AuthContext(user=user, role=role, portfolio_id=pid)

    return dependency


async def _portfolio_of_report(session: AsyncSession, report_id: uuid.UUID) -> uuid.UUID | None:
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT c.portfolio_id FROM reports r JOIN calls c ON c.id = r.call_id "
                "WHERE r.id = :rid"
            ),
            {"rid": report_id},
        )
    ).first()
    return row[0] if row else None


async def _portfolio_of_report_item(
    session: AsyncSession, item_id: uuid.UUID
) -> uuid.UUID | None:
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT c.portfolio_id FROM report_items ri "
                "JOIN reports r ON r.id = ri.report_id JOIN calls c ON c.id = r.call_id "
                "WHERE ri.id = :iid"
            ),
            {"iid": item_id},
        )
    ).first()
    return row[0] if row else None


def authorize_report(action: Action) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency for report-scoped routes (``{report_id}``). Resolves the call's portfolio."""

    async def dependency(
        report_id: uuid.UUID,
        user: CurrentUser,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> AuthContext:
        pid = await _portfolio_of_report(session, report_id)
        if pid is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
        role = await _portfolio_role(session, user.id, pid)
        if role is None or not is_allowed(role, action):
            raise _FORBIDDEN
        return AuthContext(user=user, role=role, portfolio_id=pid)

    return dependency


def authorize_report_item(action: Action) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency for report-item routes (``{item_id}``)."""

    async def dependency(
        item_id: uuid.UUID,
        user: CurrentUser,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> AuthContext:
        pid = await _portfolio_of_report_item(session, item_id)
        if pid is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="item not found")
        role = await _portfolio_role(session, user.id, pid)
        if role is None or not is_allowed(role, action):
            raise _FORBIDDEN
        return AuthContext(user=user, role=role, portfolio_id=pid)

    return dependency


def authorize_org(action: Action) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency factory for org-scoped routes (create portfolio, manage users)."""

    async def dependency(
        user: CurrentUser,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> AuthContext:
        role = await _org_role(session, user.id)
        if role is None or not is_allowed(role, action):
            raise _FORBIDDEN
        return AuthContext(user=user, role=role, portfolio_id=None)

    return dependency
