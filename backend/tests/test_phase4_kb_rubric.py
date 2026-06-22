"""Phase 4 DoD: KB upload/registration. (Rubric distillation was removed — the judge agents
read the full KB directly, so there is no per-item rubric distillation step anymore.)"""

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
    return (
        await client.post("/auth/dev-login", json={"email": email, "as_admin": as_admin})
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin_portfolio(client: AsyncClient) -> tuple[str, str]:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = (await client.post("/portfolios", json={"name": "P"}, headers=_auth(admin))).json()["id"]
    return admin, pid


async def test_kb_presign_and_register_and_list(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    presign = await client.post(
        f"/portfolios/{pid}/kb:presign",
        json={"files": [{"filename": "policy.pdf"}]},
        headers=_auth(admin),
    )
    assert presign.status_code == 200, presign.text
    key = presign.json()["uploads"][0]["key"]
    assert key.startswith(f"{pid}/")

    reg = await client.post(
        f"/portfolios/{pid}/kb",
        json={"items": [{"key": key, "sha256": "a" * 64, "page_count": 12}]},
        headers=_auth(admin),
    )
    assert reg.status_code == 201, reg.text
    listing = (await client.get(f"/portfolios/{pid}/kb", headers=_auth(admin))).json()
    # New portfolios ship with the seeded Everest KB (2 docs) + the one just registered.
    assert len(listing) == 3
    assert any(d["page_count"] == 12 for d in listing)
