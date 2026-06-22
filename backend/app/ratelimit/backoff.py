"""Backoff math + error classification for graceful retries.

Used in two places:
  * Inside HTTP clients (STT/LLM) for transient blips, via ``retry_async`` — short,
    in-call retries that respect a server ``Retry-After``.
  * By the durable worker, which persists ``next_attempt_at = now + compute_backoff(n)``
    across process restarts (the authoritative retry ladder; see §8.3).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar


class RetryableError(Exception):
    """Transient failure — safe to retry after backoff (5xx, network, timeout)."""


class RateLimitError(RetryableError):
    """429 / quota exhaustion. Carries an optional server-provided Retry-After (sec)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class FatalError(Exception):
    """Non-retryable (4xx other than 429, schema violation). Goes straight to dead-letter."""


def classify_http_status(status: int, retry_after: float | None = None) -> None:
    """Raise the appropriate error class for a non-2xx HTTP status, or return None if OK.

    429 -> RateLimitError, 5xx -> RetryableError, other 4xx -> FatalError.
    """
    if 200 <= status < 300:
        return None
    if status == 429:
        raise RateLimitError("rate limited (429)", retry_after=retry_after)
    if 500 <= status < 600:
        raise RetryableError(f"server error ({status})")
    raise FatalError(f"client error ({status})")


def compute_backoff(
    attempt: int,
    *,
    base: float,
    cap: float,
    jitter_ratio: float,
    rand: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with symmetric jitter, capped.

    ``attempt`` is 1-based (first retry = 1). Returns seconds to wait before the
    next attempt: ``min(cap, base * 2^(attempt-1))`` perturbed by +/- jitter_ratio.

    Example (base=2, cap=300): 2, 4, 8, 16, 32, ... -> capped at 300, each +/-25%.
    """
    if attempt < 1:
        attempt = 1
    raw = min(cap, base * (2.0 ** (attempt - 1)))
    # rand() in [0,1) -> jitter factor in [-jitter_ratio, +jitter_ratio)
    jitter = raw * jitter_ratio * (2.0 * rand() - 1.0)
    return max(0.0, raw + jitter)


T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base: float,
    cap: float,
    jitter_ratio: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
) -> T:
    """Call ``fn`` with retries on RetryableError/RateLimitError.

    Honours ``RateLimitError.retry_after`` when present (server knows best),
    otherwise falls back to ``compute_backoff``. ``FatalError`` and any other
    exception propagate immediately — no point retrying a 4xx or a bug.
    """
    attempt = 0
    while True:
        try:
            return await fn()
        except RateLimitError as exc:
            attempt += 1
            if attempt >= max_attempts:
                raise
            wait = exc.retry_after
            if wait is None:
                wait = compute_backoff(
                    attempt, base=base, cap=cap, jitter_ratio=jitter_ratio, rand=rand
                )
            await sleep(wait)
        except RetryableError:
            attempt += 1
            if attempt >= max_attempts:
                raise
            wait = compute_backoff(
                attempt, base=base, cap=cap, jitter_ratio=jitter_ratio, rand=rand
            )
            await sleep(wait)
