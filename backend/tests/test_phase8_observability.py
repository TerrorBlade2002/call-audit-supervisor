"""Phase 8 DoD: router tuning writes overrides from disagreement, metrics + admin endpoint."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.checklists.service import create_default_checklist
from app.db import session_scope
from app.judge.client import StubJudge
from app.judge.embeddings import StubEmbedder
from app.judge.options import ProcessingOption
from app.judge.routing import RoutingConfig
from app.judge.service import judge_call
from app.models import Agent, Call, Portfolio, RouterOverride, User, Verification
from app.observability.metrics import collect_metrics
from app.stt import Transcript, Utterance
from app.tuning.router_tuning import recompute_overrides

pytestmark = pytest.mark.usefixtures("db_ready")

_CFG = RoutingConfig(confidence_threshold=0.75, min_evidence_chars=24, max_escalation_fraction=0.6)
_TRANSCRIPT = Transcript(
    transcript_id="t-o", duration_sec=300.0, text="c",
    utterances=[Utterance(speaker="A", start_sec=0.0, end_sec=2.0, text="Recorded.")],
)


async def _seed_portfolio_with_verifier() -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope() as s:
        p = Portfolio(name="P")
        s.add(p)
        await s.flush()
        await create_default_checklist(s, p.id)
        u = User(email=f"v-{uuid.uuid4()}@x.com", name="V")
        s.add(u)
        await s.flush()
        return p.id, u.id


async def _seed_verified_report(pid: uuid.UUID, verifier_id: uuid.UUID, judgement: str) -> None:
    async with session_scope() as s:
        a = Agent(portfolio_id=pid, name="A")
        s.add(a)
        await s.flush()
        c = Call(agent_id=a.id, portfolio_id=pid, r2_audio_uri="r2://x")
        s.add(c)
        await s.flush()
        report_id = await judge_call(
            s, call_id=c.id, portfolio_id=pid, transcript=_TRANSCRIPT,
            option=ProcessingOption.CHECKLIST_ONLY,
            judge=StubJudge(), embedder=StubEmbedder(), routing_config=_CFG,
        )
        s.add(Verification(report_id=report_id, verifier_id=verifier_id, judgement=judgement))


async def test_tuning_writes_overrides_from_disagreement() -> None:
    pid, verifier_id = await _seed_portfolio_with_verifier()
    # 6 verified reports: 5 WRONG, 1 CORRECT → every checklist item has 83% disagreement.
    for i in range(6):
        await _seed_verified_report(pid, verifier_id, "WRONG" if i < 5 else "CORRECT")

    async with session_scope() as s:
        result = await recompute_overrides(s, error_threshold=0.3, min_sample=5)
    assert result.written > 0
    async with session_scope() as s:
        overrides = list(await s.scalars(select(RouterOverride)))
    assert len(overrides) == result.written


async def test_tuning_clears_overrides_when_agreement_recovers() -> None:
    pid, verifier_id = await _seed_portfolio_with_verifier()
    for _ in range(6):
        await _seed_verified_report(pid, verifier_id, "CORRECT")  # all agree now
    async with session_scope() as s:
        result = await recompute_overrides(s, error_threshold=0.3, min_sample=5)
    assert result.written == 0
    async with session_scope() as s:
        assert list(await s.scalars(select(RouterOverride))) == []


async def test_metrics_report_queue_and_agreement() -> None:
    pid, verifier_id = await _seed_portfolio_with_verifier()
    await _seed_verified_report(pid, verifier_id, "CORRECT")

    async with session_scope() as s:
        metrics = await collect_metrics(s)
    assert "queue_depth" in metrics
    assert metrics["agreement_rate"] == 1.0  # one CORRECT, zero WRONG
    assert metrics["daily_cap_per_portfolio"] > 0
    assert metrics["escalation_fraction"] >= 0.0


async def test_admin_metrics_endpoint_rbac(client: AsyncClient) -> None:
    admin = (
        await client.post("/auth/dev-login", json={"email": "a@x.com", "as_admin": True})
    ).json()["access_token"]
    nobody = (
        await client.post("/auth/dev-login", json={"email": "n@x.com"})
    ).json()["access_token"]

    ok = await client.get("/admin/metrics", headers={"Authorization": f"Bearer {admin}"})
    assert ok.status_code == 200
    assert "queue_depth" in ok.json()

    denied = await client.get("/admin/metrics", headers={"Authorization": f"Bearer {nobody}"})
    assert denied.status_code == 403
