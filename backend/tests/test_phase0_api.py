"""Phase 0 DoD: login, portfolio/agent CRUD, and RBAC denials (§2, §18 Phase 0).

DB-backed. Skips automatically if Postgres is unreachable (see conftest.client).
"""

from __future__ import annotations

from httpx import AsyncClient


async def _login(client: AsyncClient, email: str, *, as_admin: bool = False) -> str:
    resp = await client.post(
        "/auth/dev-login", json={"email": email, "as_admin": as_admin}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_unauthenticated_is_rejected(client: AsyncClient) -> None:
    # No bearer token -> HTTPBearer rejects with 401 Unauthorized.
    assert (await client.get("/portfolios")).status_code == 401


async def test_admin_creates_portfolio_and_lists_it(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    resp = await client.post("/portfolios", json={"name": "Collections A"}, headers=_auth(admin))
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]

    listing = await client.get("/portfolios", headers=_auth(admin))
    assert listing.status_code == 200
    assert [p["id"] for p in listing.json()] == [pid]


async def test_non_admin_cannot_create_portfolio(client: AsyncClient) -> None:
    user = await _login(client, "nobody@example.com")
    resp = await client.post("/portfolios", json={"name": "Nope"}, headers=_auth(user))
    assert resp.status_code == 403


async def test_non_member_sees_no_portfolios(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    await client.post("/portfolios", json={"name": "Private"}, headers=_auth(admin))
    outsider = await _login(client, "outsider@example.com")
    listing = await client.get("/portfolios", headers=_auth(outsider))
    assert listing.status_code == 200
    assert listing.json() == []


async def test_analyst_can_create_agent_but_not_delete_portfolio(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = (
        await client.post("/portfolios", json={"name": "P"}, headers=_auth(admin))
    ).json()["id"]

    analyst_token = await _login(client, "analyst@example.com")
    me = (await client.get("/me", headers=_auth(analyst_token))).json()
    # Admin assigns ANALYST membership.
    assign = await client.post(
        f"/portfolios/{pid}/members",
        json={"user_id": me["id"], "role": "ANALYST"},
        headers=_auth(admin),
    )
    assert assign.status_code == 204

    # ANALYST may create + view agents...
    created = await client.post(
        f"/portfolios/{pid}/agents", json={"name": "Agent Smith"}, headers=_auth(analyst_token)
    )
    assert created.status_code == 201, created.text
    agents = await client.get(f"/portfolios/{pid}/agents", headers=_auth(analyst_token))
    assert [a["name"] for a in agents.json()] == ["Agent Smith"]

    # ...but may NOT delete the portfolio (ADMIN-only).
    denied = await client.delete(f"/portfolios/{pid}", headers=_auth(analyst_token))
    assert denied.status_code == 403


async def test_viewer_cannot_create_agent(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = (
        await client.post("/portfolios", json={"name": "P"}, headers=_auth(admin))
    ).json()["id"]
    viewer = await _login(client, "viewer@example.com")
    me = (await client.get("/me", headers=_auth(viewer))).json()
    await client.post(
        f"/portfolios/{pid}/members",
        json={"user_id": me["id"], "role": "VIEWER"},
        headers=_auth(admin),
    )
    # VIEWER can list...
    assert (await client.get(f"/portfolios/{pid}/agents", headers=_auth(viewer))).status_code == 200
    # ...but cannot create.
    denied = await client.post(
        f"/portfolios/{pid}/agents", json={"name": "X"}, headers=_auth(viewer)
    )
    assert denied.status_code == 403


async def test_admin_can_delete_portfolio(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = (
        await client.post("/portfolios", json={"name": "Temp"}, headers=_auth(admin))
    ).json()["id"]
    resp = await client.delete(f"/portfolios/{pid}", headers=_auth(admin))
    assert resp.status_code == 204
    listing = await client.get("/portfolios", headers=_auth(admin))
    assert listing.json() == []
