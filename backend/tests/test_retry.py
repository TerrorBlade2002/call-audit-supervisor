"""Failure policy (§8.3) — retry vs dead-letter."""

from __future__ import annotations

from app.config import RateLimitSettings
from app.orchestration.retry import DeadLetter, Retry, decide_failure
from app.ratelimit.backoff import FatalError, RetryableError

_CFG = RateLimitSettings(
    RETRY_MAX_ATTEMPTS=5, RETRY_BASE_SECONDS=2.0, RETRY_CAP_SECONDS=300.0, RETRY_JITTER_RATIO=0.25
)
_NO_JITTER = lambda: 0.5  # noqa: E731 — rand=0.5 -> zero jitter


def test_first_failure_schedules_retry() -> None:
    d = decide_failure(
        attempts_before=0, max_attempts=5, error=RetryableError("x"), cfg=_CFG, rand=_NO_JITTER
    )
    assert isinstance(d, Retry)
    assert d.attempts == 1
    assert d.delay == 2.0  # base * 2^0


def test_backoff_grows_with_attempts() -> None:
    d = decide_failure(
        attempts_before=2, max_attempts=5, error=RetryableError("x"), cfg=_CFG, rand=_NO_JITTER
    )
    assert isinstance(d, Retry)
    assert d.attempts == 3
    assert d.delay == 8.0  # base * 2^2


def test_exhausted_attempts_dead_letter() -> None:
    d = decide_failure(
        attempts_before=4, max_attempts=5, error=RetryableError("x"), cfg=_CFG, rand=_NO_JITTER
    )
    assert isinstance(d, DeadLetter)
    assert d.attempts == 5
    assert "exhausted" in d.reason


def test_fatal_error_dead_letters_immediately() -> None:
    d = decide_failure(
        attempts_before=0, max_attempts=5, error=FatalError("4xx"), cfg=_CFG, rand=_NO_JITTER
    )
    assert isinstance(d, DeadLetter)
    assert d.attempts == 1
    assert "fatal" in d.reason
