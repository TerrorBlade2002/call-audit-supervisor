"""Failure policy (§8.3) — pure decision logic.

On a retryable failure: increment attempts; if exhausted → dead-letter (FAILED) + alert,
else schedule the next attempt at ``now + backoff(attempts)``. Fatal failures dead-letter
immediately. This module decides *what* to do; queue.py performs the DB write.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from app.config import RateLimitSettings
from app.ratelimit.backoff import FatalError, compute_backoff


@dataclass(frozen=True)
class Retry:
    """Keep the job in its current state; retry after ``delay`` seconds."""

    delay: float
    attempts: int


@dataclass(frozen=True)
class DeadLetter:
    """Give up: move the job to FAILED and alert."""

    reason: str
    attempts: int


Decision = Retry | DeadLetter


def decide_failure(
    *,
    attempts_before: int,
    max_attempts: int,
    error: BaseException,
    cfg: RateLimitSettings,
    rand: Callable[[], float] = random.random,
) -> Decision:
    """Decide retry-vs-dead-letter for a failed step.

    ``attempts_before`` is the job's attempt count *before* this failure; the failure
    consumes one attempt. A FatalError always dead-letters (no point retrying a 4xx).
    """
    attempts = attempts_before + 1
    if isinstance(error, FatalError):
        return DeadLetter(reason=f"fatal: {error}", attempts=attempts)
    if attempts >= max_attempts:
        return DeadLetter(
            reason=f"max attempts ({max_attempts}) exhausted: {error}", attempts=attempts
        )
    delay = compute_backoff(
        attempts,
        base=cfg.retry_base_seconds,
        cap=cfg.retry_cap_seconds,
        jitter_ratio=cfg.retry_jitter_ratio,
        rand=rand,
    )
    return Retry(delay=delay, attempts=attempts)
