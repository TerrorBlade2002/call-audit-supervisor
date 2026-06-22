"""Auth endpoints.

``/auth/dev-login`` is a development/test affordance (disabled in production) that mints
an application JWT for an email without an external IdP. The production OIDC code-exchange
endpoint will live here too, minting the *same* token shape via ``security.mint_token``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import OrgMember, Portfolio, PortfolioMember, User
from app.models import Role as RoleRow
from app.passwords import verify_password
from app.rbac import Role
from app.schemas import DevLoginRequest, LoginRequest, TokenOut, UserOut
from app.security import CurrentUser, mint_token

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=TokenOut)
async def login(
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenOut:
    """Real email + password sign-in. Roles are resolved per request from the user's grants."""
    user = await session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or user.status != "active" or not verify_password(
        body.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
        )
    return TokenOut(access_token=mint_token(user.id, user.email))


@router.post("/auth/dev-login", response_model=TokenOut)
async def dev_login(
    body: DevLoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenOut:
    if settings.env == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None:
        user = User(email=body.email, name=body.name or body.email.split("@")[0], status="active")
        session.add(user)
        await session.flush()

    if body.as_admin:
        admin_role_id = await session.scalar(select(RoleRow.id).where(RoleRow.name == Role.ADMIN))
        if admin_role_id is not None:
            exists = await session.scalar(
                select(OrgMember).where(
                    OrgMember.user_id == user.id, OrgMember.role_id == admin_role_id
                )
            )
            if exists is None:
                session.add(OrgMember(user_id=user.id, role_id=admin_role_id))
    elif body.role in (Role.SUPERVISOR, Role.AGENT):
        # Dev convenience: grant this portfolio role on every existing portfolio so the dummy
        # account can be tested. Real per-portfolio assignment goes through member management.
        role_id = await session.scalar(select(RoleRow.id).where(RoleRow.name == body.role))
        if role_id is not None:
            pids = list(await session.scalars(select(Portfolio.id)))
            for pid in pids:
                exists = await session.scalar(
                    select(PortfolioMember).where(
                        PortfolioMember.user_id == user.id, PortfolioMember.portfolio_id == pid
                    )
                )
                if exists is None:
                    session.add(
                        PortfolioMember(user_id=user.id, portfolio_id=pid, role_id=role_id)
                    )

    await session.commit()
    return TokenOut(access_token=mint_token(user.id, user.email))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
