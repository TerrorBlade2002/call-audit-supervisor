"""The durable worker loop + reconciler scheduler (§6, §8).

Claim a batch in its own transaction (committing the lease), then process each claimed job
in its own transaction so one job's failure can't roll back another's progress. External
concurrency is bounded by the per-provider limiter semaphores, not by how many we claim.
"""

from __future__ import annotations

import asyncio

import structlog

from app import retention
from app.db import session_scope
from app.orchestration import queue, reconciler
from app.orchestration.engine import EngineDeps, process_one
from app.orchestration.states import CLAIMABLE

log = structlog.get_logger("worker.loop")

_CLAIMABLE_VALUES = [s.value for s in CLAIMABLE]


async def claim_batch(deps: EngineDeps, worker_id: str) -> list[queue.ClaimedJob]:
    async with session_scope() as session:
        return await queue.claim(
            session,
            worker_id=worker_id,
            batch=deps.settings.worker.claim_batch,
            lease_seconds=deps.settings.worker.lease_seconds,
            claimable_states=_CLAIMABLE_VALUES,
        )


async def _process(job: queue.ClaimedJob, deps: EngineDeps) -> None:
    async with session_scope() as session:
        await process_one(session, job, deps)


async def tick(deps: EngineDeps, worker_id: str) -> int:
    """One claim→process cycle. Returns the number of jobs processed."""
    jobs = await claim_batch(deps, worker_id)
    if jobs:
        await asyncio.gather(*(_process(job, deps) for job in jobs))
    return len(jobs)


async def run_loop(deps: EngineDeps, worker_id: str, stop: asyncio.Event) -> None:
    """Continuously claim and process until ``stop`` is set."""
    poll = deps.settings.worker.poll_interval_seconds
    while not stop.is_set():
        try:
            processed = await tick(deps, worker_id)
        except Exception:  # noqa: BLE001 — loop must survive a bad cycle
            log.exception("worker.tick_failed")
            processed = 0
        if processed == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll)
            except TimeoutError:
                pass


async def run_reconciler(deps: EngineDeps, stop: asyncio.Event) -> None:
    """Periodically sweep for overdue transcripts / stuck jobs (§8.5)."""
    interval = deps.settings.worker.reconciler_interval_seconds
    overdue = deps.settings.worker.transcript_overdue_seconds
    while not stop.is_set():
        try:
            async with session_scope() as session:
                n = await reconciler.run_once(session, stt=deps.stt, overdue_seconds=overdue)
            if n:
                log.info("reconciler.sweep", recovered=n)
        except Exception:  # noqa: BLE001
            log.exception("reconciler.failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run_retention(deps: EngineDeps, stop: asyncio.Event) -> None:
    """Periodically purge recordings/transcripts/reports past the retention window (FR4)."""
    w = deps.settings.worker
    if not w.retention_enabled:
        log.info("retention.disabled")
        return
    while not stop.is_set():
        try:
            # Drain in batches so a large backlog doesn't hold one long transaction.
            while not stop.is_set():
                async with session_scope() as session:
                    n = await retention.run_once(
                        session, storage=deps.storage, cutoff_days=w.retention_days
                    )
                if n == 0:
                    break
        except Exception:  # noqa: BLE001
            log.exception("retention.failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=w.retention_interval_seconds)
        except TimeoutError:
            pass
