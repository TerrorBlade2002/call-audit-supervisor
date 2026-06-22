"""Seed real login credentials: a super admin + an Everest supervisor & agent.

Idempotent — re-running resets the passwords to the values below. Run from the repo root:
    PYTHONUTF8=1 PYTHONPATH=backend .venv/Scripts/python.exe backend/scripts/seed_creds.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db import session_scope
from app.models import OrgMember, Portfolio, PortfolioMember, User
from app.models import Role as RoleRow
from app.passwords import hash_password

SUPER_ADMIN = ("admin@everest.local", "EverestAdmin#2026", "Everest Admin")
SUPERVISOR = ("supervisor@everest.local", "EverestSup#2026", "Everest Supervisor")
AGENT = ("agent@everest.local", "EverestAgent#2026", "Everest Agent")


async def _ensure_role(s, name: str):
    rid = await s.scalar(select(RoleRow.id).where(RoleRow.name == name))
    if rid is None:
        r = RoleRow(name=name)
        s.add(r)
        await s.flush()
        rid = r.id
    return rid


async def _ensure_user(s, email: str, password: str, name: str) -> User:
    u = await s.scalar(select(User).where(User.email == email))
    if u is None:
        u = User(email=email, name=name, status="active", password_hash=hash_password(password))
        s.add(u)
        await s.flush()
    else:
        u.password_hash = hash_password(password)
    return u


async def main() -> None:
    async with session_scope() as s:
        admin_role = await _ensure_role(s, "ADMIN")
        sup_role = await _ensure_role(s, "SUPERVISOR")
        agent_role = await _ensure_role(s, "AGENT")

        admin = await _ensure_user(s, *SUPER_ADMIN)
        has_org = await s.scalar(
            select(OrgMember).where(
                OrgMember.user_id == admin.id, OrgMember.role_id == admin_role
            )
        )
        if has_org is None:
            s.add(OrgMember(user_id=admin.id, role_id=admin_role))

        pid = await s.scalar(select(Portfolio.id).where(Portfolio.name == "Everest"))
        if pid is None:  # fall back to the oldest portfolio if "Everest" isn't named that
            pid = await s.scalar(select(Portfolio.id).order_by(Portfolio.created_at).limit(1))
        if pid is not None:
            sup = await _ensure_user(s, *SUPERVISOR)
            ag = await _ensure_user(s, *AGENT)
            for u, rid in ((sup, sup_role), (ag, agent_role)):
                if await s.get(PortfolioMember, {"user_id": u.id, "portfolio_id": pid}) is None:
                    s.add(PortfolioMember(user_id=u.id, portfolio_id=pid, role_id=rid))
            print(f"seeded supervisor + agent into portfolio {pid}")
        else:
            print("no portfolio found — created super admin only")
        print("done")


if __name__ == "__main__":
    asyncio.run(main())
