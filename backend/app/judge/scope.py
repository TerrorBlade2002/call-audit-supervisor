"""Binding-scope resolution for super-admin-authored config (prompts, report templates, …).

A row is bound to a folder (``agent_id`` set), a whole portfolio (``agent_id`` NULL,
``portfolio_id`` set), or globally (both NULL). For a given call's (portfolio, folder) we pick the
MOST SPECIFIC in-use row: folder → portfolio → global. This is the single place the binding rule
lives, so re-pointing it later (e.g. portfolio-only, or an org tier) is a one-function change.
"""

from __future__ import annotations

import uuid
from typing import Protocol, TypeVar


class _Scoped(Protocol):
    portfolio_id: uuid.UUID | None
    agent_id: uuid.UUID | None


T = TypeVar("T", bound=_Scoped)

# Specificity ranks (higher wins). 0 = this row does not apply to the given scope.
_FOLDER = 3
_PORTFOLIO = 2
_GLOBAL = 1


def _rank(row: _Scoped, portfolio_id: uuid.UUID | None, agent_id: uuid.UUID | None) -> int:
    if row.agent_id is not None:  # folder-bound — only applies to that exact folder
        return _FOLDER if row.agent_id == agent_id else 0
    if row.portfolio_id is not None:  # portfolio-bound — only that portfolio
        return _PORTFOLIO if row.portfolio_id == portfolio_id else 0
    return _GLOBAL  # both NULL — the global/default tier, applies everywhere


def resolve_scoped(
    rows: list[T], portfolio_id: uuid.UUID | None, agent_id: uuid.UUID | None
) -> T | None:
    """Return the most-specific applicable row (folder > portfolio > global), or None.

    Assumes at most one in-use row per scope tier (enforced by the partial unique index), so
    ranks never tie within an applicable set.
    """
    best: T | None = None
    best_rank = 0
    for row in rows:
        rank = _rank(row, portfolio_id, agent_id)
        if rank > best_rank:
            best, best_rank = row, rank
    return best
