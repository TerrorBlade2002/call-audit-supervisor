"""Runtime lookup of super-admin-authored agent prompt bodies (Prompt Builder, §B).

The judge agents compose their system instruction as: body + code-owned output directive +
impartiality clause. ``body`` is the in_use AgentPrompt for that agent, or the hard-coded
default below. Only the BODY is editable — the output directive (structured-JSON contract) and
impartiality clause are always appended by the agent, so determinism can't be edited away.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.judge.prompts import (
    CHECKLIST_AGENT_PROMPT,
    FEEDBACK_AGENT_PROMPT,
    MERGED_AGENT_PROMPT,
    REWRITER_AGENT_PROMPT,
)
from app.judge.scope import resolve_scoped
from app.models import AgentPrompt

# The editable agents. "merged" is the combined feedback+checklist agent used by the FULL option.
AGENTS = ("feedback", "checklist", "ideal", "merged")

DEFAULT_BODY: dict[str, str] = {
    "feedback": FEEDBACK_AGENT_PROMPT,
    "checklist": CHECKLIST_AGENT_PROMPT,
    "ideal": REWRITER_AGENT_PROMPT,
    "merged": MERGED_AGENT_PROMPT,
}


async def load_active_prompts(
    session: AsyncSession,
    portfolio_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> dict[str, str]:
    """{judge-agent: in_use prompt body} resolved for this call's (portfolio, folder).

    Most-specific binding wins (folder → portfolio → global). Judge-agents with no applicable
    in_use row are omitted, so the agent falls back to its hard-coded default body.
    """
    rows = list(await session.scalars(select(AgentPrompt).where(AgentPrompt.in_use.is_(True))))
    out: dict[str, str] = {}
    for ag in AGENTS:
        chosen = resolve_scoped([r for r in rows if r.agent == ag], portfolio_id, agent_id)
        if chosen is not None:
            out[ag] = chosen.content
    return out
