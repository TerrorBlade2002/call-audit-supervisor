"""Authentication: JWT mint/verify + the ``current_user`` dependency.

v1 trust model: an OIDC provider authenticates the human; we exchange that for a
short-lived application JWT (HS256, ``JWT_SECRET``) carrying the user id. Every API
dependency below validates that JWT. The OIDC *code exchange* is added later behind this
same contract — ``current_user`` does not change when it lands. A dev-login endpoint
(non-production only) mints the same JWT so development and tests need no external IdP.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import User

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)

_bearer = HTTPBearer(auto_error=True)


def mint_token(user_id: uuid.UUID, email: str) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + TOKEN_TTL).timestamp()),
    }
    return str(jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM))


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the authenticated user from the bearer JWT, or raise 401."""
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[ALGORITHM])
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError) as exc:
        raise cred_exc from exc

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or user.status != "active":
        raise cred_exc
    return user


CurrentUser = Annotated[User, Depends(current_user)]
