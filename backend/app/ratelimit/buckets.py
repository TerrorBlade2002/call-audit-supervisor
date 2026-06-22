"""In-process throughput shaping: token buckets + concurrency, per external provider.

A ``ProviderLimiter`` bundles the three constraints a vendor imposes:
  * RPM  — requests/minute   (a token bucket, 1 token per request)
  * TPM  — tokens/minute     (a token bucket, N tokens per request, N = est. tokens)
  * concurrency — max in-flight calls (a semaphore)

Sized *under* the provider's published limits (see config.RateLimitSettings) so we
shape our own traffic before the vendor ever returns a 429. When a 429 does slip
through, backoff.retry_async handles it. At pilot scale one worker process makes the
in-process bucket authoritative; for N workers, swap the bucket for a Postgres/Redis
shared counter (the ProviderLimiter interface stays identical — §8.6).

Clocks are injectable (``time_fn``/``sleep_fn``) so tests run deterministically with
a fake clock instead of real sleeps.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic

from app.config import RateLimitSettings

TimeFn = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]


class TokenBucket:
    """Classic refilling token bucket. ``capacity`` tokens, refilled at ``rate``/sec.

    ``acquire(n)`` blocks until n tokens are available, then deducts them. An ``n``
    larger than capacity is clamped to capacity (so a single oversized request can
    still make progress instead of deadlocking forever).
    """

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float,
        *,
        time_fn: TimeFn = monotonic,
        sleep_fn: SleepFn = asyncio.sleep,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self._rate = rate_per_sec
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._time = time_fn
        self._sleep = sleep_fn
        self._last = time_fn()
        self._lock = asyncio.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now

    async def acquire(self, amount: float = 1.0) -> None:
        amount = min(float(amount), self._capacity)
        while True:
            async with self._lock:
                now = self._time()
                self._refill(now)
                if self._tokens >= amount:
                    self._tokens -= amount
                    return
                deficit = amount - self._tokens
                wait = deficit / self._rate
            # Sleep outside the lock so other coroutines can refill-check meanwhile.
            await self._sleep(wait)

    @property
    def available(self) -> float:
        """Current token estimate (refilled to now). For metrics/tests."""
        self._refill(self._time())
        return self._tokens


class ProviderLimiter:
    """Combined RPM + TPM + concurrency guard for a single external provider."""

    def __init__(
        self,
        name: str,
        *,
        rpm: int,
        tpm: int | None,
        max_concurrency: int,
        time_fn: TimeFn = monotonic,
        sleep_fn: SleepFn = asyncio.sleep,
    ) -> None:
        self.name = name
        self._rpm_bucket = TokenBucket(rpm / 60.0, rpm, time_fn=time_fn, sleep_fn=sleep_fn)
        self._tpm_bucket = (
            TokenBucket(tpm / 60.0, tpm, time_fn=time_fn, sleep_fn=sleep_fn)
            if tpm is not None
            else None
        )
        self._sem = asyncio.Semaphore(max_concurrency)

    @asynccontextmanager
    async def guard(self, *, est_tokens: int = 0) -> AsyncIterator[None]:
        """Acquire all relevant limits, run the body, release concurrency on exit.

        ``est_tokens`` feeds the TPM bucket (use a conservative over-estimate of the
        request's input+output tokens). RPM/TPM tokens are consumed, not returned —
        they model rate, not occupancy; only the concurrency slot is released.
        """
        await self._sem.acquire()
        try:
            await self._rpm_bucket.acquire(1)
            if self._tpm_bucket is not None and est_tokens > 0:
                await self._tpm_bucket.acquire(est_tokens)
            yield
        finally:
            self._sem.release()


@dataclass(frozen=True)
class Limiters:
    """The provider limiters a worker process holds. Built once at startup."""

    gemini: ProviderLimiter
    assemblyai: ProviderLimiter


def build_limiters(
    cfg: RateLimitSettings,
    *,
    time_fn: TimeFn = monotonic,
    sleep_fn: SleepFn = asyncio.sleep,
) -> Limiters:
    """Construct provider limiters from config. Single source of truth for limits."""
    return Limiters(
        gemini=ProviderLimiter(
            "gemini",
            rpm=cfg.gemini_rpm,
            tpm=cfg.gemini_tpm,
            max_concurrency=cfg.gemini_max_concurrency,
            time_fn=time_fn,
            sleep_fn=sleep_fn,
        ),
        assemblyai=ProviderLimiter(
            "assemblyai",
            rpm=cfg.aai_rpm,
            tpm=None,  # AAI bills by audio-hour, not tokens; RPM + in-flight is enough.
            max_concurrency=cfg.aai_max_inflight,
            time_fn=time_fn,
            sleep_fn=sleep_fn,
        ),
    )
