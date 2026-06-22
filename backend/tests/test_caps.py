"""Per-portfolio daily cap: defer (not fail) over-cap work (NFR3, §8.6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.ratelimit.caps import DailyCap, InMemoryDailyUsageStore


async def test_cap_allows_up_to_limit_then_defers() -> None:
    store = InMemoryDailyUsageStore()
    fixed = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    cap = DailyCap(store, cap=3, now_fn=lambda: fixed)
    pid = uuid.uuid4()

    decisions = [await cap.check_and_reserve(pid) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert decisions[2].used == 3
    assert decisions[3].deferred is True
    assert decisions[3].remaining == 0


async def test_cap_is_per_portfolio() -> None:
    store = InMemoryDailyUsageStore()
    fixed = datetime(2026, 6, 12, tzinfo=UTC)
    cap = DailyCap(store, cap=1, now_fn=lambda: fixed)
    a, b = uuid.uuid4(), uuid.uuid4()

    assert (await cap.check_and_reserve(a)).allowed is True
    assert (await cap.check_and_reserve(a)).allowed is False  # a exhausted
    assert (await cap.check_and_reserve(b)).allowed is True   # b independent


async def test_cap_resets_across_days() -> None:
    store = InMemoryDailyUsageStore()
    day = {"d": datetime(2026, 6, 12, tzinfo=UTC)}
    cap = DailyCap(store, cap=1, now_fn=lambda: day["d"])
    pid = uuid.uuid4()

    assert (await cap.check_and_reserve(pid)).allowed is True
    assert (await cap.check_and_reserve(pid)).allowed is False
    day["d"] = datetime(2026, 6, 13, tzinfo=UTC)  # next day
    assert (await cap.check_and_reserve(pid)).allowed is True
