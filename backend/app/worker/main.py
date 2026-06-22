"""Worker entrypoint.

Phase 2 fills in the durable loop (claim with FOR UPDATE SKIP LOCKED, dispatch by
state, retry/backoff/dead-letter) and the reconciler sweep. For now this is a runnable
skeleton that wires config + limiters so the container boots and the shape is fixed.
"""

from __future__ import annotations

import asyncio
import signal
import socket

import httpx
import structlog

from app.config import settings
from app.judge.client import GeminiJudge, JudgeClient, StubJudge
from app.judge.embeddings import Embedder, GeminiEmbedder, StubEmbedder
from app.judge.gemini import build_gemini_client
from app.judge.merged import GeminiMerged, MergedGenerator, StubMerged
from app.judge.narrative import GeminiNarrative, NarrativeGenerator, StubNarrative
from app.judge.subjective import GeminiSubjective, StubSubjective, SubjectiveGenerator
from app.notifier import PgNotifier
from app.orchestration.engine import EngineDeps, default_make_daily_cap
from app.orchestration.stubs import StubStt
from app.ratelimit import build_limiters
from app.storage import StorageService, build_storage
from app.stt import WEBHOOK_AUTH_HEADER
from app.stt.assemblyai import AssemblyAIClient
from app.worker.loop import run_loop, run_reconciler, run_retention

log = structlog.get_logger("worker")


def _build_stt(http: httpx.AsyncClient) -> object:
    """Real AssemblyAI client when an API key is set, else the stub (local dev)."""
    if not settings.assemblyai_api_key:
        log.warning("worker.stt_stub", reason="ASSEMBLYAI_API_KEY unset")
        return StubStt()
    webhook_url = (
        f"{settings.public_base_url.rstrip('/')}/webhooks/assemblyai"
        if settings.public_base_url
        else None
    )
    return AssemblyAIClient(
        api_key=settings.assemblyai_api_key,
        base_url=settings.assemblyai_base_url,
        retry=settings.ratelimit,
        client=http,
        webhook_url=webhook_url,
        webhook_auth_header=WEBHOOK_AUTH_HEADER,
        webhook_secret=settings.assemblyai_webhook_secret,
    )


def _build_judge(gclient: object) -> JudgeClient:
    if gclient is None:
        log.warning("worker.judge_stub", reason="GEMINI_API_KEY unset")
        return StubJudge()
    # Multimodal SDK judge (audio + transcript + thinking) on the Developer API.
    return GeminiJudge(
        client=gclient, model=settings.gemini_model_primary, retry=settings.ratelimit
    )


def _build_subjective(gclient: object) -> SubjectiveGenerator:
    if gclient is None:
        return StubSubjective()
    return GeminiSubjective(
        client=gclient, model=settings.gemini_model_primary, retry=settings.ratelimit
    )


def _build_rewriter(gclient: object) -> NarrativeGenerator:
    if gclient is None:
        return StubNarrative()
    return GeminiNarrative(
        client=gclient, model=settings.gemini_model_primary, retry=settings.ratelimit
    )


def _build_merged(gclient: object) -> MergedGenerator:
    if gclient is None:
        return StubMerged()
    return GeminiMerged(
        client=gclient, model=settings.gemini_model_primary, retry=settings.ratelimit
    )


def _build_embedder(http: httpx.AsyncClient) -> Embedder:
    if not settings.gemini_api_key:
        return StubEmbedder()
    return GeminiEmbedder(
        api_key=settings.gemini_api_key,
        model=settings.gemini_embedding_model,
        retry=settings.ratelimit,
        client=http,
    )


def build_deps(http: httpx.AsyncClient) -> EngineDeps:
    """Assemble engine dependencies. Real Gemini/AssemblyAI when keys are set, else stubs."""
    storage: StorageService = build_storage(settings)
    gclient = build_gemini_client(settings) if settings.gemini_api_key else None
    return EngineDeps(
        settings=settings,
        limiters=build_limiters(settings.ratelimit),
        make_daily_cap=default_make_daily_cap(settings),
        stt=_build_stt(http),  # type: ignore[arg-type]
        judge=_build_judge(gclient),
        embedder=_build_embedder(http),
        storage=storage,
        notifier=PgNotifier(),
        merged=_build_merged(gclient),
        subjective=_build_subjective(gclient),
        rewriter=_build_rewriter(gclient),
    )


async def run() -> None:
    worker_id = f"{socket.gethostname()}:{id(object())}"
    http = httpx.AsyncClient(timeout=30.0)
    deps = build_deps(http)
    log.info(
        "worker.start",
        env=settings.env,
        worker_id=worker_id,
        gemini_rpm=settings.ratelimit.gemini_rpm,
        aai_inflight=settings.ratelimit.aai_max_inflight,
        daily_cap=settings.ratelimit.daily_cap_per_portfolio,
    )

    stop = asyncio.Event()

    def _handle(*_: object) -> None:
        log.info("worker.shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle)
        except NotImplementedError:  # Windows dev
            pass

    _try_apply_lifecycle()

    try:
        await asyncio.gather(
            run_loop(deps, worker_id, stop),
            run_reconciler(deps, stop),
            run_retention(deps, stop),
        )
    finally:
        await http.aclose()


def _try_apply_lifecycle() -> None:
    """Best-effort: set native R2 lifecycle (recordings/transcripts/reports expire after the
    retention window; KB exempt). Needs a bucket-admin token; with an object-scoped token
    this is denied and the app-level retention sweep is the enforcement path instead."""
    if not settings.r2_endpoint_url:
        return
    from app.storage import build_s3_client, ensure_lifecycle

    s3 = build_s3_client(settings)
    days = settings.worker.retention_days
    for bucket in (
        settings.r2_bucket_recordings,
        settings.r2_bucket_transcripts,
        settings.r2_bucket_reports,
    ):
        try:
            ensure_lifecycle(s3, bucket, days=days)
            log.info("lifecycle.applied", bucket=bucket, days=days)
        except Exception as exc:  # noqa: BLE001 — best-effort; ANY failure (perms, network,
            # DNS, R2 unreachable at boot) must not crash the worker. The app-level retention
            # sweep enforces FR4 regardless.
            log.warning(
                "lifecycle.skipped", bucket=bucket,
                reason="R2 lifecycle not applied (perms/network); app-level retention enforces FR4",
                error=str(exc)[:120],
            )


def main() -> None:
    from app.logconfig import force_utf8_stdio

    force_utf8_stdio()  # Windows cp1252 stdout would crash on non-ASCII log content.
    asyncio.run(run())


if __name__ == "__main__":
    main()
