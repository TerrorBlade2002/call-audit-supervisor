"""Checklist builder + versioning service (FR6.2/FR6.3).

Versioning rule (the DoD invariant): a checklist version is **immutable once used by a
report**. Editing an unused checklist mutates it in place; editing one that a report
already references creates a new version (same ``family_id``, ``version`` + 1) and archives
the old — so historical reports keep pointing at the exact items they were judged against.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checklists.seed import DEFAULT_CHECKLIST_NAME, DEFAULT_ITEMS
from app.models import Checklist, ChecklistItem, Report

_ITEM_FIELDS = ("section", "text", "answer_type", "is_subjective", "risk", "guidance")


def _add_items(session: AsyncSession, checklist_id: uuid.UUID, items: list[dict[str, Any]]) -> None:
    for i, item in enumerate(items):
        session.add(
            ChecklistItem(
                checklist_id=checklist_id,
                sort_order=item.get("sort_order", i),
                section=item["section"],
                text=item["text"],
                answer_type=item["answer_type"],
                is_subjective=bool(item.get("is_subjective", False)),
                risk=item.get("risk", "NORMAL"),
                guidance=item.get("guidance"),
                options=item.get("options"),
            )
        )


async def create_default_checklist(session: AsyncSession, portfolio_id: uuid.UUID) -> Checklist:
    """Seed the shipped default checklist for a new portfolio (FR6.1)."""
    checklist = Checklist(
        portfolio_id=portfolio_id,
        family_id=uuid.uuid4(),
        name=DEFAULT_CHECKLIST_NAME,
        is_default=True,
        version=1,
        status="active",
    )
    session.add(checklist)
    await session.flush()
    _add_items(session, checklist.id, [dict(i) for i in DEFAULT_ITEMS])
    await session.flush()
    return checklist


async def create_checklist(
    session: AsyncSession,
    portfolio_id: uuid.UUID,
    *,
    name: str,
    items: list[dict[str, Any]],
    requires_kb: bool = True,
) -> Checklist:
    checklist = Checklist(
        portfolio_id=portfolio_id,
        family_id=uuid.uuid4(),
        name=name,
        is_default=False,
        version=1,
        status="active",
        requires_kb=requires_kb,
    )
    session.add(checklist)
    await session.flush()
    _add_items(session, checklist.id, items)
    await session.flush()
    return checklist


async def is_used_by_report(session: AsyncSession, checklist_id: uuid.UUID) -> bool:
    found = await session.scalar(select(Report.id).where(Report.checklist_id == checklist_id))
    return found is not None


async def update_checklist(
    session: AsyncSession,
    checklist: Checklist,
    *,
    name: str,
    items: list[dict[str, Any]],
    requires_kb: bool | None = None,
) -> Checklist:
    """Edit in place if unused; otherwise publish a new immutable version."""
    keep_kb = checklist.requires_kb if requires_kb is None else requires_kb
    if not await is_used_by_report(session, checklist.id):
        checklist.name = name
        checklist.requires_kb = keep_kb
        # Replace items wholesale (the builder sends the full desired state).
        await session.execute(
            delete(ChecklistItem).where(ChecklistItem.checklist_id == checklist.id)
        )
        _add_items(session, checklist.id, items)
        await session.flush()
        return checklist

    # Used by a report → new version, archive the old.
    checklist.status = "archived"
    new_version = Checklist(
        portfolio_id=checklist.portfolio_id,
        family_id=checklist.family_id,
        name=name,
        is_default=checklist.is_default,
        version=checklist.version + 1,
        status="active",
        requires_kb=keep_kb,
    )
    session.add(new_version)
    await session.flush()
    _add_items(session, new_version.id, items)
    await session.flush()
    return new_version


async def list_active_checklists(
    session: AsyncSession, portfolio_id: uuid.UUID
) -> list[Checklist]:
    rows = await session.scalars(
        select(Checklist)
        .where(Checklist.portfolio_id == portfolio_id, Checklist.status != "archived")
        .order_by(Checklist.created_at)
    )
    return list(rows)


async def get_default_checklist(
    session: AsyncSession, portfolio_id: uuid.UUID
) -> Checklist | None:
    """The portfolio's active default checklist (latest version), used to judge calls."""
    result: Checklist | None = await session.scalar(
        select(Checklist)
        .where(
            Checklist.portfolio_id == portfolio_id,
            Checklist.is_default.is_(True),
            Checklist.status == "active",
        )
        .order_by(Checklist.version.desc())
        .limit(1)
    )
    return result


async def get_items(session: AsyncSession, checklist_id: uuid.UUID) -> list[ChecklistItem]:
    rows = await session.scalars(
        select(ChecklistItem)
        .where(ChecklistItem.checklist_id == checklist_id)
        .order_by(ChecklistItem.sort_order)
    )
    return list(rows)


async def latest_version(
    session: AsyncSession, family_id: uuid.UUID
) -> int:
    return int(
        await session.scalar(
            select(func.max(Checklist.version)).where(Checklist.family_id == family_id)
        )
        or 0
    )
