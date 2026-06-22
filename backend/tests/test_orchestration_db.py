"""Phase 2 DoD (§18) — the durable engine proven against a real Postgres.

Covers: full traversal to DONE, lease/crash recovery, retry→dead-letter, lost-webhook
recovery via the reconciler, daily-cap defer (not fail), and concurrent-claim safety.
Skips automatically when Postgres is unavailable (conftest.db_ready).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.checklists.service import create_default_checklist
from app.config import RateLimitSettings, get_settings
from app.db import session_scope
from app.judge.client import StubJudge
from app.judge.embeddings import StubEmbedder
from app.judge.merged import StubMerged
from app.judge.narrative import StubNarrative
from app.judge.subjective import StubSubjective
from app.models import Agent, Call, Portfolio
from app.orchestration import queue, reconciler
from app.orchestration.engine import EngineDeps, default_make_daily_cap
from app.orchestration.stubs import StubStt
from app.ratelimit import build_limiters
from app.storage import FakeStorage

pytestmark = pytest.mark.usefixtures("db_ready")


# --- helpers ---------------------------------------------------------------

async def _seed_job(*, max_attempts: int = 5) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with session_scope() as s:
        p = Portfolio(name="P")
        s.add(p)
        await s.flush()
        a = Agent(portfolio_id=p.id, name="A")
        s.add(a)
        await s.flush()
        c = Call(agent_id=a.id, portfolio_id=p.id, r2_audio_uri="r2://audio/x.wav")
        s.add(c)
        await s.flush()
        # The judge step needs the portfolio's default checklist.
        await create_default_checklist(s, p.id)
        job_id = await queue.enqueue(
            s, call_id=c.id, portfolio_id=p.id, max_attempts=max_attempts
        )
    return p.id, c.id, job_id


async def _state(job_id: uuid.UUID) -> str:
    async with session_scope() as s:
        return await s.scalar(text("SELECT state FROM jobs WHERE id = :id"), {"id": job_id})


async def _last_error(job_id: uuid.UUID) -> str | None:
    async with session_scope() as s:
        return await s.scalar(text("SELECT last_error FROM jobs WHERE id = :id"), {"id": job_id})


def _deps(*, stt=None, judge=None, ratelimit: RateLimitSettings | None = None) -> EngineDeps:
    settings = get_settings()
    if ratelimit is not None:
        settings = settings.model_copy(update={"ratelimit": ratelimit})
    return EngineDeps(
        settings=settings,
        limiters=build_limiters(settings.ratelimit),
        make_daily_cap=default_make_daily_cap(settings),
        stt=stt or StubStt(),
        judge=judge or StubJudge(),
        embedder=StubEmbedder(),
        storage=FakeStorage(),
        merged=StubMerged(),
        subjective=StubSubjective(),
        rewriter=StubNarrative(),
    )


async def _tick(deps: EngineDeps, worker: str = "w1") -> int:
    from app.worker.loop import tick

    return await tick(deps, worker)


# --- tests -----------------------------------------------------------------

async def test_job_traverses_all_states_to_done() -> None:
    _pid, call_id, job_id = await _seed_job()
    deps = _deps()

    await _tick(deps)  # PENDING_TRANSCRIPTION -> AWAITING_TRANSCRIPT (parked)
    assert await _state(job_id) == "AWAITING_TRANSCRIPT"

    assert await _tick(deps) == 0  # parked job is NOT claimed by the loop

    # Simulate the AssemblyAI webhook arriving.
    async with session_scope() as s:
        advanced = await queue.advance_to_judge_by_transcript(s, f"stub-transcript-{call_id}")
    assert advanced is True
    assert await _state(job_id) == "PENDING_JUDGE"

    await _tick(deps)  # PENDING_JUDGE -> DONE
    assert await _state(job_id) == "DONE"


async def test_transcript_materialized_before_done() -> None:
    _pid, call_id, job_id = await _seed_job()
    deps = _deps()

    await _tick(deps)  # -> AWAITING_TRANSCRIPT
    async with session_scope() as s:
        await queue.advance_to_judge_by_transcript(s, f"stub-transcript-{call_id}")
    await _tick(deps)  # PENDING_JUDGE: materialize transcript + judge -> DONE

    assert await _state(job_id) == "DONE"
    # The judge step fetched + stored the transcript and recorded its URI on the call.
    async with session_scope() as s:
        uri = await s.scalar(
            text("SELECT r2_transcript_uri FROM calls WHERE id = :id"), {"id": call_id}
        )
    assert uri == f"{call_id}.json"


async def test_redelivered_webhook_is_noop() -> None:
    _pid, call_id, job_id = await _seed_job()
    deps = _deps()
    await _tick(deps)
    async with session_scope() as s:
        first = await queue.advance_to_judge_by_transcript(s, f"stub-transcript-{call_id}")
    async with session_scope() as s:
        second = await queue.advance_to_judge_by_transcript(s, f"stub-transcript-{call_id}")
    assert first is True
    assert second is False  # idempotent redelivery


async def test_expired_lease_is_reclaimed_after_crash() -> None:
    _pid, _call_id, job_id = await _seed_job()

    # Claim (acquires a lease) but "crash" before processing.
    async with session_scope() as s:
        claimed = await queue.claim(
            s, worker_id="dead", batch=10, lease_seconds=300,
            claimable_states=["PENDING_TRANSCRIPTION"],
        )
    assert [c.id for c in claimed] == [job_id]

    # While the lease is live, another worker cannot reclaim it.
    async with session_scope() as s:
        again = await queue.claim(
            s, worker_id="live", batch=10, lease_seconds=300,
            claimable_states=["PENDING_TRANSCRIPTION"],
        )
    assert again == []

    # Expire the lease (simulate time passing past the dead worker's lease).
    async with session_scope() as s:
        await s.execute(
            text("UPDATE jobs SET locked_until = now() - interval '1 second' WHERE id = :id"),
            {"id": job_id},
        )
    async with session_scope() as s:
        reclaimed = await queue.claim(
            s, worker_id="live", batch=10, lease_seconds=300,
            claimable_states=["PENDING_TRANSCRIPTION"],
        )
    assert [c.id for c in reclaimed] == [job_id]


async def test_forced_failures_retry_then_dead_letter() -> None:
    _pid, _call_id, job_id = await _seed_job(max_attempts=2)
    # base=0 -> zero backoff so retries are immediately due (no real waiting).
    rl = RateLimitSettings(
        RETRY_MAX_ATTEMPTS=2, RETRY_BASE_SECONDS=0.0, RETRY_CAP_SECONDS=0.0, RETRY_JITTER_RATIO=0.0
    )
    deps = _deps(stt=StubStt(fail_times=99), ratelimit=rl)

    await _tick(deps)  # attempt 1 -> Retry
    assert await _state(job_id) == "PENDING_TRANSCRIPTION"

    await _tick(deps)  # attempt 2 -> exhausted -> FAILED
    assert await _state(job_id) == "FAILED"
    assert "exhausted" in (await _last_error(job_id) or "")


async def test_lost_webhook_recovered_by_reconciler() -> None:
    _pid, _call_id, job_id = await _seed_job()
    deps = _deps(stt=StubStt(poll_status="ready"))
    await _tick(deps)  # -> AWAITING_TRANSCRIPT
    assert await _state(job_id) == "AWAITING_TRANSCRIPT"

    # Webhook never arrives. overdue_seconds=0 => all parked jobs are overdue now.
    async with session_scope() as s:
        recovered = await reconciler.run_once(s, stt=deps.stt, overdue_seconds=0)
    assert recovered == 1
    assert await _state(job_id) == "PENDING_JUDGE"

    await _tick(deps)
    assert await _state(job_id) == "DONE"


async def test_daily_cap_defers_not_fails() -> None:
    _pid, _call_id, job_id = await _seed_job()
    # cap=0 -> every submission is over-cap and must defer.
    rl = RateLimitSettings(DAILY_CAP_PER_PORTFOLIO=0)
    deps = _deps(ratelimit=rl)

    await _tick(deps)
    # Deferred: stays claimable state, NOT failed, with a user-visible reason.
    assert await _state(job_id) == "PENDING_TRANSCRIPTION"
    assert "daily limit" in (await _last_error(job_id) or "")


async def test_concurrent_claims_never_double_process() -> None:
    job_ids = set()
    for _ in range(6):
        _pid, _call_id, jid = await _seed_job()
        job_ids.add(jid)

    async def claim(worker: str) -> list[uuid.UUID]:
        async with session_scope() as s:
            rows = await queue.claim(
                s, worker_id=worker, batch=3, lease_seconds=300,
                claimable_states=["PENDING_TRANSCRIPTION"],
            )
        return [r.id for r in rows]

    a, b = await asyncio.gather(claim("w1"), claim("w2"))
    # Disjoint claims (FOR UPDATE SKIP LOCKED) and no id claimed twice.
    assert set(a).isdisjoint(set(b))
    assert len(a) + len(b) == len(set(a) | set(b))
