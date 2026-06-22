"""Central configuration.

Every operational number — rate limits, caps, backoff, routing thresholds — lives
here as a default and is override-able by environment variable (NFR3: "flexible so
numbers and limits could be changed/modified later on"). Nothing is hardcoded at a
call site; code reads ``settings`` instead.

The defaults below are *derived* from the PRD reference volume (§5):
    2 portfolios x 10 agents x 30 recordings/day x 22 days = 13,200 calls/mo
    -> ~600 calls/day total, ~300/portfolio/day, ~1,100 audio-hours/mo.
See docs/RATE_LIMITS_AND_COST.md for the full derivation.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RateLimitSettings(BaseSettings):
    """Provider quotas + our self-imposed caps. All env-override-able.

    These are deliberately conservative — they sit *under* provider limits so we
    never trip a vendor 429 in the common case, and degrade gracefully when we do.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- Gemini (Developer API) guardrails -----------------------------------
    # PILOT defaults: sized for one worker process with modest async concurrency, well
    # under a paid Tier-1 Gemini Pro quota. Sustained judge throughput ≈ concurrency × 60 /
    # judge_latency_s; at concurrency=4 and ~45s/judge that's ~5 calls/min — a burst of 10
    # drains in ~2 min, 20 in ~4 min. For production raise GEMINI_MAX_CONCURRENCY (and RPM/
    # TPM to match your tier) via env — a config change, not code (§19).
    gemini_rpm: int = Field(default=60, alias="GEMINI_RPM")
    gemini_tpm: int = Field(default=1_000_000, alias="GEMINI_TPM")
    gemini_rpd: int = Field(default=4_000, alias="GEMINI_RPD")
    gemini_max_concurrency: int = Field(default=4, alias="GEMINI_MAX_CONCURRENCY")

    # --- AssemblyAI guardrails ----------------------------------------------
    # Async submit-and-webhook: "concurrency" = in-flight transcripts, not threads.
    # Transcription is the cheap/fast leg; 8 in-flight is plenty for the pilot.
    aai_max_inflight: int = Field(default=8, alias="AAI_MAX_INFLIGHT")
    aai_rpm: int = Field(default=60, alias="AAI_RPM")

    # --- Per-portfolio daily cap (calls submitted to transcription / day) ----
    # PILOT default = 100/portfolio/day (~10 agents × ~10 recordings) — caps worst-case spend
    # while allowing real testing. Full volume is ~300; raise via env when you go live.
    daily_cap_per_portfolio: int = Field(default=100, alias="DAILY_CAP_PER_PORTFOLIO")

    # --- Retry / backoff (exponential + jitter, capped) ----------------------
    retry_max_attempts: int = Field(default=5, alias="RETRY_MAX_ATTEMPTS")
    retry_base_seconds: float = Field(default=2.0, alias="RETRY_BASE_SECONDS")
    retry_cap_seconds: float = Field(default=300.0, alias="RETRY_CAP_SECONDS")
    retry_jitter_ratio: float = Field(default=0.25, alias="RETRY_JITTER_RATIO")


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    claim_batch: int = Field(default=8, alias="WORKER_CLAIM_BATCH")
    lease_seconds: int = Field(default=300, alias="WORKER_LEASE_SECONDS")
    poll_interval_seconds: float = Field(default=2.0, alias="WORKER_POLL_INTERVAL_SECONDS")
    reconciler_interval_seconds: int = Field(default=180, alias="RECONCILER_INTERVAL_SECONDS")
    transcript_overdue_seconds: int = Field(
        default=900, alias="RECONCILER_TRANSCRIPT_OVERDUE_SECONDS"
    )
    # Retention (FR4): recordings + transcripts + reports are purged after this many days;
    # the KB is exempt (kept forever). Enforced app-side because R2 lifecycle needs a
    # bucket-admin token; set RETENTION_ENABLED=false to disable the sweep.
    retention_enabled: bool = Field(default=True, alias="RETENTION_ENABLED")
    retention_days: int = Field(default=30, alias="RETENTION_DAYS")
    retention_interval_seconds: int = Field(default=86400, alias="RETENTION_INTERVAL_SECONDS")


class RouterSettings(BaseSettings):
    """Judge escalation thresholds (§7.3) — tuned without redeploy."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    confidence_threshold: float = Field(default=0.75, alias="ROUTER_CONFIDENCE_THRESHOLD")
    min_evidence_chars: int = Field(default=24, alias="ROUTER_MIN_EVIDENCE_CHARS")
    max_escalation_fraction: float = Field(default=0.6, alias="ROUTER_MAX_ESCALATION_FRACTION")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    env: str = Field(default="local", alias="ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    # Public base URL of the api service (for the AssemblyAI webhook callback). When unset
    # (local dev), STT runs reconciler-poll-only — no webhook.
    public_base_url: str = Field(default="", alias="PUBLIC_BASE_URL")

    database_url: str = Field(
        default="postgresql+asyncpg://everest:everest@localhost:5432/everest",
        alias="DATABASE_URL",
    )

    # R2 / object storage
    r2_endpoint_url: str = Field(default="", alias="R2_ENDPOINT_URL")
    r2_access_key_id: str = Field(default="", alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str = Field(default="", alias="R2_SECRET_ACCESS_KEY")
    r2_bucket_recordings: str = Field(default="everest-recordings", alias="R2_BUCKET_RECORDINGS")
    r2_bucket_transcripts: str = Field(default="everest-transcripts", alias="R2_BUCKET_TRANSCRIPTS")
    r2_bucket_kb: str = Field(default="everest-kb", alias="R2_BUCKET_KB")
    r2_bucket_reports: str = Field(default="everest-reports", alias="R2_BUCKET_REPORTS")
    r2_presign_ttl_seconds: int = Field(default=900, alias="R2_PRESIGN_TTL_SECONDS")
    # Local-dev fallback when R2 is not configured: shared transcript dir for api+worker.
    dev_storage_dir: str = Field(default=".devdata", alias="DEV_STORAGE_DIR")

    # External AI
    assemblyai_api_key: str = Field(default="", alias="ASSEMBLYAI_API_KEY")
    assemblyai_webhook_secret: str = Field(default="", alias="ASSEMBLYAI_WEBHOOK_SECRET")
    assemblyai_base_url: str = Field(
        default="https://api.assemblyai.com", alias="ASSEMBLYAI_BASE_URL"
    )
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model_primary: str = Field(
        default="gemini-3.1-pro-preview", alias="GEMINI_MODEL_PRIMARY"
    )
    gemini_embedding_model: str = Field(
        default="gemini-embedding-001", alias="GEMINI_EMBEDDING_MODEL"
    )

    # Auth
    oidc_issuer: str = Field(default="", alias="OIDC_ISSUER")
    oidc_client_id: str = Field(default="", alias="OIDC_CLIENT_ID")
    oidc_client_secret: str = Field(default="", alias="OIDC_CLIENT_SECRET")
    jwt_secret: str = Field(default="change-me-in-prod", alias="JWT_SECRET")

    # Eval gate (§16.2)
    eval_agreement_threshold: float = Field(default=0.9, alias="EVAL_AGREEMENT_THRESHOLD")
    eval_gold_set_path: str = Field(
        default="backend/eval/gold_set.json", alias="EVAL_GOLD_SET_PATH"
    )

    # Nested config groups
    ratelimit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    router: RouterSettings = Field(default_factory=RouterSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
