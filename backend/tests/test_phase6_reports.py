"""Phase 6 DoD: report renders (verdicts/evidence/objections), lazy narrative caches,
notes persist, no numeric scores, RBAC enforced. DB-backed via the ASGI client.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

import app.api.reports as reports_api
from app.checklists.service import create_default_checklist
from app.db import session_scope
from app.judge.embeddings import StubEmbedder
from app.judge.merged import StubMerged
from app.judge.narrative import StubNarrative
from app.judge.options import ProcessingOption
from app.judge.routing import RoutingConfig
from app.judge.service import judge_call
from app.models import Agent, Call, Portfolio
from app.storage import FakeStorage
from app.stt import Transcript, Utterance

_CFG = RoutingConfig(confidence_threshold=0.75, min_evidence_chars=24, max_escalation_fraction=0.6)
_TRANSCRIPT = Transcript(
    transcript_id="t-r",
    duration_sec=300.0,
    text="recorded call",
    utterances=[Utterance(speaker="A", start_sec=0.0, end_sec=2.0, text="All calls recorded.")],
)


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(reports_api, "_storage", fake)
    return fake


async def _login(client: AsyncClient, email: str, *, as_admin: bool = False) -> str:
    resp = await client.post("/auth/dev-login", json={"email": email, "as_admin": as_admin})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_report(fake: FakeStorage) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope() as s:
        p = Portfolio(name="P")
        s.add(p)
        await s.flush()
        await create_default_checklist(s, p.id)
        a = Agent(portfolio_id=p.id, name="A")
        s.add(a)
        await s.flush()
        c = Call(agent_id=a.id, portfolio_id=p.id, r2_audio_uri="r2://x")
        s.add(c)
        await s.flush()
        await fake.put_transcript(c.id, _TRANSCRIPT)
        report_id = await judge_call(
            s, call_id=c.id, portfolio_id=p.id, transcript=_TRANSCRIPT,
            option=ProcessingOption.FULL, merged_gen=StubMerged(),
            rewriter_gen=StubNarrative(), embedder=StubEmbedder(), routing_config=_CFG,
        )
    return p.id, report_id


async def test_report_renders_with_lazy_narrative(
    client: AsyncClient, fake_storage: FakeStorage
) -> None:
    _pid, report_id = await _seed_report(fake_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)

    resp = await client.get(f"/reports/{report_id}", headers=_auth(admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["items"]) > 0
    # PASS/FAIL/NA only — no numeric scores anywhere in the payload (§12).
    assert all(i["answer"] in (None, "PASS", "FAIL", "NA") for i in body["items"])
    assert "score" not in resp.text.lower()
    assert body["objections"]
    # Narrative generated lazily on first open.
    assert body["narrative"] is not None
    assert "coaching" in body["narrative"]


async def test_narrative_is_cached_after_first_open(
    client: AsyncClient, fake_storage: FakeStorage
) -> None:
    _pid, report_id = await _seed_report(fake_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)
    first = (await client.get(f"/reports/{report_id}", headers=_auth(admin))).json()["narrative"]
    second = (await client.get(f"/reports/{report_id}", headers=_auth(admin))).json()["narrative"]
    assert first == second  # cached, not regenerated


async def test_user_note_persists(client: AsyncClient, fake_storage: FakeStorage) -> None:
    _pid, report_id = await _seed_report(fake_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)
    body = (await client.get(f"/reports/{report_id}", headers=_auth(admin))).json()
    item_id = body["items"][0]["id"]

    patch = await client.patch(
        f"/report-items/{item_id}/note", json={"note": "Reviewed — agree."}, headers=_auth(admin)
    )
    assert patch.status_code == 204
    after = (await client.get(f"/reports/{report_id}", headers=_auth(admin))).json()
    assert next(i for i in after["items"] if i["id"] == item_id)["user_note"] == "Reviewed — agree."


async def test_agent_name_override_persists_and_survives_reprocessing(
    client: AsyncClient, fake_storage: FakeStorage
) -> None:
    pid, report_id = await _seed_report(fake_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)
    body = (await client.get(f"/reports/{report_id}", headers=_auth(admin))).json()
    call_id = uuid.UUID(body["call_id"])

    # Auditor overrides the agent name → reflected on read.
    patch = await client.patch(
        f"/reports/{report_id}/agent-name",
        json={"agent_name": "Jane Auditor"}, headers=_auth(admin),
    )
    assert patch.status_code == 204
    after = (await client.get(f"/reports/{report_id}", headers=_auth(admin))).json()
    assert after["agent_name"] == "Jane Auditor"

    # Re-process the call (rebuilds the report row) — override lives on the call, so it survives.
    async with session_scope() as s:
        new_report_id = await judge_call(
            s, call_id=call_id, portfolio_id=pid, transcript=_TRANSCRIPT,
            option=ProcessingOption.FULL, merged_gen=StubMerged(),
            rewriter_gen=StubNarrative(), embedder=StubEmbedder(), routing_config=_CFG,
        )
    reproc = (await client.get(f"/reports/{new_report_id}", headers=_auth(admin))).json()
    assert reproc["agent_name"] == "Jane Auditor"  # durable across re-judge

    # Clearing the override reverts to the auto-extracted value (None for the stub).
    clear = await client.patch(
        f"/reports/{new_report_id}/agent-name", json={"agent_name": ""}, headers=_auth(admin)
    )
    assert clear.status_code == 204
    cleared = (await client.get(f"/reports/{new_report_id}", headers=_auth(admin))).json()
    assert cleared["agent_name"] is None


async def test_viewer_can_read_but_not_note(
    client: AsyncClient, fake_storage: FakeStorage
) -> None:
    pid, report_id = await _seed_report(fake_storage)
    admin = await _login(client, "admin@example.com", as_admin=True)
    viewer = await _login(client, "viewer@example.com")
    me = (await client.get("/me", headers=_auth(viewer))).json()
    await client.post(
        f"/portfolios/{pid}/members",
        json={"user_id": me["id"], "role": "VIEWER"},
        headers=_auth(admin),
    )

    assert (await client.get(f"/reports/{report_id}", headers=_auth(viewer))).status_code == 200
    admin_view = (await client.get(f"/reports/{report_id}", headers=_auth(admin))).json()
    item_id = admin_view["items"][0]["id"]
    denied = await client.patch(
        f"/report-items/{item_id}/note", json={"note": "x"}, headers=_auth(viewer)
    )
    assert denied.status_code == 403
