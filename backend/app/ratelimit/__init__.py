"""Rate limiting, daily caps, and retry/backoff (NFR3).

Design goals:
  * Never exceed Gemini / AssemblyAI quotas (token-bucket RPM/TPM + concurrency).
  * Per-portfolio daily cap that *defers* (re-queues) rather than fails over-cap work.
  * Graceful 429 handling: exponential backoff with jitter, capped, Retry-After aware.
  * Every limit is config (config.py / env) so it tunes without a redeploy.

Two layers, by purpose:
  * In-process throughput shaping  -> buckets.ProviderLimiter (RPM/TPM/concurrency).
  * Durable per-portfolio quota    -> caps.DailyCap (Postgres counter, multi-worker safe).
"""

from app.ratelimit.backoff import (
    FatalError,
    RateLimitError,
    RetryableError,
    classify_http_status,
    compute_backoff,
    retry_async,
)
from app.ratelimit.buckets import ProviderLimiter, TokenBucket, build_limiters
from app.ratelimit.caps import CapDecision, DailyCap

__all__ = [
    "FatalError",
    "RateLimitError",
    "RetryableError",
    "classify_http_status",
    "compute_backoff",
    "retry_async",
    "ProviderLimiter",
    "TokenBucket",
    "build_limiters",
    "CapDecision",
    "DailyCap",
]
