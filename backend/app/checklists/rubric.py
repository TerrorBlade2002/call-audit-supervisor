"""Knowledge-base text loading for the judge agents (§7.3).

The 3-agent pipeline feeds the full operational documents to the agents directly, so there is
no rubric distillation (no extra LLM call to pre-grind the KB into per-item rubric slices).
This module just assembles the portfolio's KB text, capped for the per-call multimodal request.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document

# Cap KB context fed to an agent (keeps the per-call request bounded; KB ≤120 pages).
_KB_CHAR_CAP = 400_000


async def _kb_context(
    session: AsyncSession,
    portfolio_id: uuid.UUID,
    doc_ids: list[uuid.UUID] | None = None,
) -> str:
    q = select(Document.text).where(
        Document.portfolio_id == portfolio_id, Document.text.isnot(None)
    )
    if doc_ids:  # a selected subset (None/empty = the portfolio's full default set)
        q = q.where(Document.id.in_(doc_ids))
    texts = await session.scalars(q)
    return "\n\n".join(t for t in texts if t)


async def load_kb_text(
    session: AsyncSession,
    portfolio_id: uuid.UUID,
    doc_ids: list[uuid.UUID] | None = None,
) -> str | None:
    """The selected KB docs as one text blob (capped), for the judge agents.

    ``doc_ids`` selects a subset; None/empty means the portfolio's full default doc set. Each
    judge agent receives the operational documents directly so they can "go through the attached
    documents thoroughly". Returns None when there is no KB text to give.
    """
    text = await _kb_context(session, portfolio_id, doc_ids)
    if not text:
        return None
    return text[:_KB_CHAR_CAP]
