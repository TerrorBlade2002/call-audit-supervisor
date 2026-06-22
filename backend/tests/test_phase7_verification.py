"""Phase 7 DoD (FR12): verification submit + audit, recording download, transcript,
and the judge↔verifier agreement metric. DB-backed via the ASGI client.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

import app.api.verification as verification_api
from app.checklists.service import create_default_checklist
from app.db import session_scope
from app.judge.client import StubJudge
from app.judge.embeddings import StubEmbedder
from app.judge.options import ProcessingOption
from app.judge.routing import RoutingConfig
from app.judge.service import judge_call
from app.models import Agent, Call, Portfolio
from app.storage import FakeStorage
from app.stt import Transcript, Utterance

_CFG = RoutingConfig(confidence_threshold=0.75, min_evidence_chars=24, max_escalation_fraction=0.6)
_TRANSCRIPT = Transcript(
    transcript_id="t-v", duration_sec=300.0, text="call",
    utterances=[Utterance(speaker="A", start_sec=0.0, end_sec=2.0, text="Recorded.")],
)


@pytest.fixture
def verif_storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(verification_api, "_storage", fake)
    return fake


async def _login(client: AsyncClient, email: str, *, as_admin: bool = False) -> str:
    resp = await client.post("/auth/dev-login", json={"email": email, "as_admin": as_admin})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_report(
    fake: FakeStorage, portfolio_id: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope() as s:
        if portfolio_id is None:
            p = Portfolio(name="P")
            s.add(p)
            await s.flush()
            await create_default_checklist(s, p.id)
            portfolio_id = p.id
        a = Agent(portfolio_id=portfolio_id, name="A")
        s.add(a)
        await s.flush()
        c = Call(agent_id=a.id, portfolio_id=portfolio_id, r2_audio_uri="r2://x.wav")
        s.add(c)
        await s.flush()
        await fake.put_transcript(c.id, _TRANSCRIPT)
        report_id = await judge_call(
            s, call_id=c.id, portfolio_id=portfolio_id, transcript=_TRANSCRIPT,
            option=ProcessingOption.CHECKLIST_ONLY,
            judge=StubJudge(), embedder=StubEmbedder(), routing_config=_CFG,
        )
    return portfolio_id, report_id


async def _make_verifier(client: AsyncClient, admin: str, pid: uuid.UUID, email: str) -> str:
    token = await _login(client, email)
    me = (await client.get("/me", headers=_auth(token))).json()
    await client.post(
        f"/portfolios/{pid}/members",
        json={"user_id": me["id"], "role": "VERIFIER"},
        headers=_auth(admin),
    )
    return token


async def test_verifier_submits_judgement(client: AsyncClient, verif_storage: FakeStorage) -> None:
    pid, report_id = await _seed_report(verif_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)
    verifier = await _make_verifier(client, admin, pid, "verifier@example.com")

    resp = await client.post(
        f"/reports/{report_id}/verification",
        json={"judgement": "CORRECT", "notes": "Agree with the verdicts."},
        headers=_auth(verifier),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["judgement"] == "CORRECT"


async def test_non_verifier_cannot_submit(client: AsyncClient, verif_storage: FakeStorage) -> None:
    pid, report_id = await _seed_report(verif_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)
    analyst = await _login(client, "analyst@example.com")
    me = (await client.get("/me", headers=_auth(analyst))).json()
    await client.post(
        f"/portfolios/{pid}/members",
        json={"user_id": me["id"], "role": "ANALYST"},
        headers=_auth(admin),
    )
    resp = await client.post(
        f"/reports/{report_id}/verification",
        json={"judgement": "WRONG"},
        headers=_auth(analyst),
    )
    assert resp.status_code == 403


async def test_recording_download_is_presigned(
    client: AsyncClient, verif_storage: FakeStorage
) -> None:
    pid, report_id = await _seed_report(verif_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)
    verifier = await _make_verifier(client, admin, pid, "verifier@example.com")
    resp = await client.get(
        f"/reports/{report_id}/recording:download", headers=_auth(verifier)
    )
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("http")
    assert resp.json()["expires_in"] > 0


async def test_transcript_is_returned(client: AsyncClient, verif_storage: FakeStorage) -> None:
    pid, report_id = await _seed_report(verif_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)
    verifier = await _make_verifier(client, admin, pid, "verifier@example.com")
    resp = await client.get(f"/reports/{report_id}/transcript", headers=_auth(verifier))
    assert resp.status_code == 200
    assert resp.json()["utterances"][0]["speaker"] == "A"


async def test_agreement_metric(client: AsyncClient, verif_storage: FakeStorage) -> None:
    pid, r1 = await _seed_report(verif_storage)
    _pid, r2 = await _seed_report(verif_storage, portfolio_id=pid)
    _pid2, r3 = await _seed_report(verif_storage, portfolio_id=pid)
    admin = await _login(client, "admin@example.com", as_admin=True)
    verifier = await _make_verifier(client, admin, pid, "verifier@example.com")

    for report_id, judgement in ((r1, "CORRECT"), (r2, "WRONG"), (r3, "CANT_SAY")):
        await client.post(
            f"/reports/{report_id}/verification",
            json={"judgement": judgement},
            headers=_auth(verifier),
        )

    stats = (await client.get(f"/portfolios/{pid}/verification-stats", headers=_auth(admin))).json()
    assert stats["total"] == 3
    assert stats["correct"] == 1
    assert stats["wrong"] == 1
    assert stats["cant_say"] == 1
    assert stats["agreement_rate"] == 0.5  # correct / (correct + wrong), CANT_SAY excluded
