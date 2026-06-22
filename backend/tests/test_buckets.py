"""Token-bucket throughput shaping (NFR3, §8.6)."""

from __future__ import annotations

import pytest

from app.config import RateLimitSettings
from app.ratelimit.buckets import ProviderLimiter, TokenBucket, build_limiters
from tests.conftest import FakeClock


async def test_bucket_allows_burst_up_to_capacity() -> None:
    clock = FakeClock()
    b = TokenBucket(10.0, 10, time_fn=clock.time, sleep_fn=clock.sleep)
    # Full bucket: 10 immediate acquisitions, no time advance.
    for _ in range(10):
        await b.acquire(1)
    assert clock.t == 0.0
    assert b.available == pytest.approx(0.0, abs=1e-6)


async def test_bucket_waits_to_refill() -> None:
    clock = FakeClock()
    b = TokenBucket(10.0, 10, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(10):
        await b.acquire(1)
    # Bucket empty; need 5 more -> wait 5/10 = 0.5s.
    await b.acquire(5)
    assert clock.t == pytest.approx(0.5)


async def test_bucket_oversized_request_clamped_not_deadlocked() -> None:
    clock = FakeClock()
    b = TokenBucket(10.0, 10, time_fn=clock.time, sleep_fn=clock.sleep)
    # Asking for more than capacity must still return (clamped to capacity).
    await b.acquire(50)
    assert b.available <= 10.0


async def test_provider_limiter_guard_consumes_rpm() -> None:
    clock = FakeClock()
    lim = ProviderLimiter(
        "gemini", rpm=60, tpm=600, max_concurrency=4, time_fn=clock.time, sleep_fn=clock.sleep
    )
    async with lim.guard(est_tokens=100):
        pass
    # 60 rpm -> 1 token/sec, capacity 60. One request consumed; no wait needed yet.
    assert clock.t == 0.0


async def test_build_limiters_from_config() -> None:
    cfg = RateLimitSettings(
        GEMINI_RPM=120,
        GEMINI_TPM=1_000_000,
        GEMINI_MAX_CONCURRENCY=8,
        AAI_RPM=60,
        AAI_MAX_INFLIGHT=32,
    )
    lims = build_limiters(cfg)
    assert lims.gemini.name == "gemini"
    assert lims.assemblyai.name == "assemblyai"
