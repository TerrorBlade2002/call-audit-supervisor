"""Runtime lookup of super-admin-authored output schemas (Agent Studio, phase 2).

When a stage has an in-use custom schema for the call's (portfolio, folder), it is used as Gemini's
response schema instead of the built-in Pydantic model. Resolution is most-specific-first
(folder → portfolio → global); no row → the built-in schema. Same pattern as prompt_store.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.judge.scope import resolve_scoped
from app.models import OutputSchema

STAGES = ("feedback", "checklist", "ideal", "merged")


async def load_active_schemas(
    session: AsyncSession,
    portfolio_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> dict[str, dict[str, Any]]:
    """{stage: JSON schema} resolved for this call's (portfolio, folder). Stages with no
    applicable in-use row are omitted (→ the stage uses its built-in Pydantic schema)."""
    rows = list(await session.scalars(select(OutputSchema).where(OutputSchema.in_use.is_(True))))
    out: dict[str, dict[str, Any]] = {}
    for st in STAGES:
        chosen = resolve_scoped([r for r in rows if r.agent == st], portfolio_id, agent_id)
        if chosen is not None and isinstance(chosen.content, dict):
            out[st] = chosen.content
    return out
