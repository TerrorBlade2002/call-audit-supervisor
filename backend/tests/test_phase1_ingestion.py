"""Phase 1 DoD (§18): presigned upload issuance + call registration + attribution + RBAC.

DB-backed via the ASGI client. R2 creds are monkeypatched to dummy values so presigning
works offline (no network).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.fixture(autouse=True)
def _dummy_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "r2_endpoint_url", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setattr(settings, "r2_access_key_id", "test-key")
    monkeypatch.setattr(settings, "r2_secret_access_key", "test-secret")


async def _login(client: AsyncClient, email: str, *, as_admin: bool = False) -> str:
    resp = await client.post("/auth/dev-login", json={"email": email, "as_admin": as_admin})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _portfolio_and_agent(client: AsyncClient, admin: str) -> tuple[str, str]:
    pid = (await client.post("/portfolios", json={"name": "P"}, headers=_auth(admin))).json()["id"]
    aid = (
        await client.post(
            f"/portfolios/{pid}/agents", json={"name": "Agent A"}, headers=_auth(admin)
        )
    ).json()["id"]
    return pid, aid


async def test_presign_returns_scoped_urls(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid, aid = await _portfolio_and_agent(client, admin)

    resp = await client.post(
        f"/portfolios/{pid}/agents/{aid}/uploads:presign",
        json={"files": [{"filename": "a.mp3"}, {"filename": "b.wav"}]},
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["uploads"]) == 2
    for up in body["uploads"]:
        assert up["key"].startswith(f"{pid}/{aid}/")
        assert up["upload_url"].startswith("https://example.r2.cloudflarestorage.com")


async def test_presign_rejects_batch_over_ten(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid, aid = await _portfolio_and_agent(client, admin)
    files = [{"filename": f"{i}.mp3"} for i in range(11)]  # NFR2: max 10
    resp = await client.post(
        f"/portfolios/{pid}/agents/{aid}/uploads:presign",
        json={"files": files},
        headers=_auth(admin),
    )
    assert resp.status_code == 422


async def test_register_creates_calls_and_enqueues_jobs(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid, aid = await _portfolio_and_agent(client, admin)

    keys = [f"{pid}/{aid}/file1.mp3", f"{pid}/{aid}/file2.mp3"]
    resp = await client.post(
        f"/portfolios/{pid}/agents/{aid}/calls",
        json={"items": [{"key": k} for k in keys]},
        headers=_auth(admin),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["batch_id"]
    # Each registered call has a job in PENDING_TRANSCRIPTION (attribution to this agent).
    assert len(body["calls"]) == 2
    for c in body["calls"]:
        assert c["agent_id"] == aid
        assert c["status"] == "PENDING_TRANSCRIPTION"

    listing = await client.get(
        f"/portfolios/{pid}/agents/{aid}/calls", headers=_auth(admin)
    )
    assert {c["status"] for c in listing.json()} == {"PENDING_TRANSCRIPTION"}


async def test_register_rejects_key_outside_agent_scope(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid, aid = await _portfolio_and_agent(client, admin)
    resp = await client.post(
        f"/portfolios/{pid}/agents/{aid}/calls",
        json={"items": [{"key": "someone-else/evil/file.mp3"}]},
        headers=_auth(admin),
    )
    assert resp.status_code == 400


async def test_viewer_cannot_upload_or_register(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid, aid = await _portfolio_and_agent(client, admin)
    viewer = await _login(client, "viewer@example.com")
    me = (await client.get("/me", headers=_auth(viewer))).json()
    await client.post(
        f"/portfolios/{pid}/members",
        json={"user_id": me["id"], "role": "VIEWER"},
        headers=_auth(admin),
    )
    presign = await client.post(
        f"/portfolios/{pid}/agents/{aid}/uploads:presign",
        json={"files": [{"filename": "a.mp3"}]},
        headers=_auth(viewer),
    )
    assert presign.status_code == 403
    register = await client.post(
        f"/portfolios/{pid}/agents/{aid}/calls",
        json={"items": [{"key": f"{pid}/{aid}/a.mp3"}]},
        headers=_auth(viewer),
    )
    assert register.status_code == 403
