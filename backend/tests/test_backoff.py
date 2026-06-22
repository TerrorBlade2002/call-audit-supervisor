"""Backoff math + error classification + retry wrapper (NFR3, §8.3)."""

from __future__ import annotations

import pytest

from app.ratelimit.backoff import (
    FatalError,
    RateLimitError,
    RetryableError,
    classify_http_status,
    compute_backoff,
    retry_async,
)
from tests.conftest import FakeClock


def test_compute_backoff_exponential_no_jitter() -> None:
    # rand()=0.5 -> jitter factor (2*0.5-1)=0 -> exact exponential.
    half = lambda: 0.5  # noqa: E731
    seqs = [compute_backoff(n, base=2, cap=300, jitter_ratio=0.25, rand=half) for n in range(1, 6)]
    assert seqs == [2, 4, 8, 16, 32]


def test_compute_backoff_capped() -> None:
    half = lambda: 0.5  # noqa: E731
    # 2 * 2^19 is huge; must clamp to cap.
    assert compute_backoff(20, base=2, cap=300, jitter_ratio=0.25, rand=half) == 300


def test_compute_backoff_jitter_bounds() -> None:
    # raw at attempt 3 (base 2) = 8; jitter +/-25% => [6, 10].
    lo = compute_backoff(3, base=2, cap=300, jitter_ratio=0.25, rand=lambda: 0.0)
    hi = compute_backoff(3, base=2, cap=300, jitter_ratio=0.25, rand=lambda: 1.0)
    assert lo == pytest.approx(6.0)
    assert hi == pytest.approx(10.0)


def test_compute_backoff_never_negative() -> None:
    assert compute_backoff(1, base=0.1, cap=300, jitter_ratio=2.0, rand=lambda: 0.0) >= 0.0


def test_classify_http_status() -> None:
    assert classify_http_status(200) is None
    assert classify_http_status(204) is None
    with pytest.raises(RateLimitError):
        classify_http_status(429)
    with pytest.raises(RetryableError):
        classify_http_status(503)
    with pytest.raises(FatalError):
        classify_http_status(400)
    with pytest.raises(FatalError):
        classify_http_status(404)


def test_classify_429_is_retryable_subclass() -> None:
    # RateLimitError must be retryable so retry_async catches it.
    assert issubclass(RateLimitError, RetryableError)


async def test_retry_async_succeeds_after_transient_failures() -> None:
    clock = FakeClock()
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("boom")
        return "ok"

    result = await retry_async(
        flaky, max_attempts=5, base=2, cap=300, jitter_ratio=0.0, sleep=clock.sleep
    )
    assert result == "ok"
    assert calls["n"] == 3
    assert clock.t == pytest.approx(2 + 4)  # two backoff sleeps


async def test_retry_async_honors_retry_after() -> None:
    clock = FakeClock()
    calls = {"n": 0}

    async def rate_limited() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError("429", retry_after=42.0)
        return "ok"

    await retry_async(
        rate_limited, max_attempts=5, base=2, cap=300, jitter_ratio=0.0, sleep=clock.sleep
    )
    assert clock.t == pytest.approx(42.0)  # server's Retry-After wins over computed backoff


async def test_retry_async_gives_up_at_max_attempts() -> None:
    clock = FakeClock()

    async def always_fail() -> str:
        raise RetryableError("nope")

    with pytest.raises(RetryableError):
        await retry_async(
            always_fail, max_attempts=3, base=1, cap=10, jitter_ratio=0.0, sleep=clock.sleep
        )


async def test_retry_async_does_not_retry_fatal() -> None:
    clock = FakeClock()
    calls = {"n": 0}

    async def fatal() -> str:
        calls["n"] += 1
        raise FatalError("4xx")

    with pytest.raises(FatalError):
        await retry_async(
            fatal, max_attempts=5, base=1, cap=10, jitter_ratio=0.0, sleep=clock.sleep
        )
    assert calls["n"] == 1  # no retries on fatal
