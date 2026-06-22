"""Per-portfolio daily cap (NFR3, §8.6).

Bounds worst-case spend: each portfolio may submit at most ``daily_cap_per_portfolio``
calls to transcription per UTC day. Over-cap calls are **deferred** (re-queued for the
next window with a user-visible "daily limit reached"), never failed — failing would
strand work and violate the zero-stranded-calls guarantee.

The check is a single atomic UPSERT so it is correct under concurrent workers:
two workers racing the same portfolio's last remaining slot cannot both win.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class CapDecision:
    allowed: bool
    used: int
    cap: int

    @property
    def deferred(self) -> bool:
        return not self.allowed

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.used)


class DailyUsageStore(Protocol):
    """Atomic counter store. Swappable: Postgres in prod, in-memory in tests."""

    async def try_increment(self, portfolio_id: UUID, day: date, cap: int) -> CapDecision:
        """Increment the portfolio's counter for ``day`` iff it is below ``cap``.

        Returns a decision; ``allowed`` is False when already at/over cap (no increment).
        """
        ...


class PostgresDailyUsageStore:
    """Atomic UPSERT against the ``daily_usage`` table. Multi-worker safe."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_increment(self, portfolio_id: UUID, day: date, cap: int) -> CapDecision:
        # ON CONFLICT ... WHERE: when at/over cap the UPDATE is skipped and nothing is
        # returned, so we know to defer. When under cap (or first call) we get the new count.
        # INSERT ... SELECT ... WHERE :cap > 0 guards the *initial* insert (so cap<=0 defers
        # immediately); the ON CONFLICT WHERE guards every subsequent increment. Together they
        # match the in-memory store exactly: at most ``cap`` submissions per portfolio per day.
        stmt = text(
            """
            INSERT INTO daily_usage (portfolio_id, day, calls_submitted)
            SELECT :pid, :day, 1 WHERE :cap > 0
            ON CONFLICT (portfolio_id, day)
            DO UPDATE SET calls_submitted = daily_usage.calls_submitted + 1
            WHERE daily_usage.calls_submitted < :cap
            RETURNING calls_submitted
            """
        )
        result = await self._session.execute(stmt, {"pid": portfolio_id, "day": day, "cap": cap})
        row = result.first()
        if row is not None:
            return CapDecision(allowed=True, used=int(row[0]), cap=cap)

        # Deferred: read the current value for the user-facing message.
        current = await self._session.execute(
            text(
                "SELECT calls_submitted FROM daily_usage "
                "WHERE portfolio_id = :pid AND day = :day"
            ),
            {"pid": portfolio_id, "day": day},
        )
        used_row = current.first()
        used = int(used_row[0]) if used_row is not None else cap
        return CapDecision(allowed=False, used=used, cap=cap)


class InMemoryDailyUsageStore:
    """Test/dev double. Not multi-process safe (fine for single-worker dev + unit tests)."""

    def __init__(self) -> None:
        self._counts: dict[tuple[UUID, date], int] = {}

    async def try_increment(self, portfolio_id: UUID, day: date, cap: int) -> CapDecision:
        key = (portfolio_id, day)
        used = self._counts.get(key, 0)
        if used >= cap:
            return CapDecision(allowed=False, used=used, cap=cap)
        used += 1
        self._counts[key] = used
        return CapDecision(allowed=True, used=used, cap=cap)


class DailyCap:
    """Façade combining a usage store with the configured cap and a UTC clock."""

    def __init__(
        self,
        store: DailyUsageStore,
        cap: int,
        *,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._cap = cap
        self._now = now_fn

    async def check_and_reserve(self, portfolio_id: UUID) -> CapDecision:
        """Reserve one slot for the current UTC day, or return a deferred decision."""
        today = self._now().date()
        return await self._store.try_increment(portfolio_id, today, self._cap)
