"""AssemblyAI webhook receiver (Phase 3 DoD §18). DB-backed via the ASGI client."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.config import settings
from app.db import session_scope
from app.models import Agent, Call, Portfolio
from app.orchestration import queue
from app.stt import WEBHOOK_AUTH_HEADER

_SECRET = "webhook-secret-123"


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "assemblyai_webhook_secret", _SECRET)


async def _seed_awaiting(transcript_id: str) -> uuid.UUID:
    async with session_scope() as s:
        p = Portfolio(name="P")
        s.add(p)
        await s.flush()
        a = Agent(portfolio_id=p.id, name="A")
        s.add(a)
        await s.flush()
        c = Call(agent_id=a.id, portfolio_id=p.id, r2_audio_uri="r2://x.wav")
        s.add(c)
        await s.flush()
        job_id = await queue.enqueue(s, call_id=c.id, portfolio_id=p.id, max_attempts=5)
        await s.execute(
            text(
                "UPDATE jobs SET state='AWAITING_TRANSCRIPT', transcript_id=:t WHERE id=:id"
            ),
            {"t": transcript_id, "id": job_id},
        )
    return job_id


async def _state(job_id: uuid.UUID) -> str:
    async with session_scope() as s:
        return await s.scalar(text("SELECT state FROM jobs WHERE id=:id"), {"id": job_id})


def _hdr() -> dict[str, str]:
    return {WEBHOOK_AUTH_HEADER: _SECRET}


async def test_unverified_webhook_rejected(client: AsyncClient) -> None:
    job_id = await _seed_awaiting("t-unsigned")
    # No / wrong auth header → 401, job untouched.
    resp = await client.post(
        "/webhooks/assemblyai", json={"transcript_id": "t-unsigned", "status": "completed"}
    )
    assert resp.status_code == 401
    bad = await client.post(
        "/webhooks/assemblyai",
        json={"transcript_id": "t-unsigned", "status": "completed"},
        headers={WEBHOOK_AUTH_HEADER: "wrong"},
    )
    assert bad.status_code == 401
    assert await _state(job_id) == "AWAITING_TRANSCRIPT"


async def test_completed_webhook_advances_job(client: AsyncClient) -> None:
    job_id = await _seed_awaiting("t-ok")
    resp = await client.post(
        "/webhooks/assemblyai",
        json={"transcript_id": "t-ok", "status": "completed"},
        headers=_hdr(),
    )
    assert resp.status_code == 200
    assert await _state(job_id) == "PENDING_JUDGE"


async def test_redelivered_webhook_is_noop(client: AsyncClient) -> None:
    job_id = await _seed_awaiting("t-dup")
    payload = {"transcript_id": "t-dup", "status": "completed"}
    first = await client.post("/webhooks/assemblyai", json=payload, headers=_hdr())
    second = await client.post("/webhooks/assemblyai", json=payload, headers=_hdr())
    assert first.status_code == 200
    assert second.status_code == 200  # idempotent redelivery
    assert await _state(job_id) == "PENDING_JUDGE"


async def test_missing_transcript_id_is_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/webhooks/assemblyai", json={"status": "completed"}, headers=_hdr()
    )
    assert resp.status_code == 400


async def test_error_status_fails_job(client: AsyncClient) -> None:
    job_id = await _seed_awaiting("t-err")
    resp = await client.post(
        "/webhooks/assemblyai",
        json={"transcript_id": "t-err", "status": "error"},
        headers=_hdr(),
    )
    assert resp.status_code == 200
    assert await _state(job_id) == "FAILED"
