"""Phase 4 DoD: default checklist seed + builder CRUD + versioning. DB-backed."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.checklists.seed import DEFAULT_CHECKLIST_NAME, DEFAULT_ITEMS
from app.db import session_scope
from app.models import Agent, Call, Report


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


_ITEM = {"section": "S", "text": "Did X", "answer_type": "PASS_FAIL"}


async def test_default_checklist_seeded_on_portfolio_create(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    listing = (await client.get(f"/portfolios/{pid}/checklists", headers=_auth(admin))).json()
    assert len(listing) == 1
    cl = listing[0]
    assert cl["is_default"] is True
    assert cl["name"] == DEFAULT_CHECKLIST_NAME
    assert cl["version"] == 1

    detail = (
        await client.get(f"/portfolios/{pid}/checklists/{cl['id']}", headers=_auth(admin))
    ).json()
    assert len(detail["items"]) == len(DEFAULT_ITEMS)
    # Compliance items carry the CRITICAL risk that drives routing.
    assert any(i["risk"] == "CRITICAL" for i in detail["items"])


async def test_create_custom_checklist(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    resp = await client.post(
        f"/portfolios/{pid}/checklists",
        json={"name": "Custom", "items": [_ITEM, {**_ITEM, "text": "Did Y"}]},
        headers=_auth(admin),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"] == 1
    assert [i["text"] for i in body["items"]] == ["Did X", "Did Y"]


async def test_update_unused_checklist_edits_in_place(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    created = (
        await client.post(
            f"/portfolios/{pid}/checklists",
            json={"name": "C", "items": [_ITEM, {**_ITEM, "text": "Did Y"}]},
            headers=_auth(admin),
        )
    ).json()
    updated = await client.put(
        f"/portfolios/{pid}/checklists/{created['id']}",
        json={"name": "C v2", "items": [{**_ITEM, "text": "Only Z"}]},
        headers=_auth(admin),
    )
    body = updated.json()
    assert body["id"] == created["id"]      # same row (in-place)
    assert body["version"] == 1
    assert body["name"] == "C v2"
    assert [i["text"] for i in body["items"]] == ["Only Z"]


async def test_update_used_checklist_creates_new_version(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    created = (
        await client.post(
            f"/portfolios/{pid}/checklists",
            json={"name": "Used", "items": [_ITEM]},
            headers=_auth(admin),
        )
    ).json()

    # Make a report reference this checklist → it becomes immutable.
    async with session_scope() as s:
        a = Agent(portfolio_id=uuid.UUID(pid), name="A")
        s.add(a)
        await s.flush()
        c = Call(agent_id=a.id, portfolio_id=uuid.UUID(pid), r2_audio_uri="r2://x")
        s.add(c)
        await s.flush()
        s.add(Report(call_id=c.id, checklist_id=uuid.UUID(created["id"])))

    updated = (
        await client.put(
            f"/portfolios/{pid}/checklists/{created['id']}",
            json={"name": "Used", "items": [{**_ITEM, "text": "Changed"}]},
            headers=_auth(admin),
        )
    ).json()
    assert updated["id"] != created["id"]          # new version row
    assert updated["version"] == 2
    assert updated["family_id"] == created["family_id"]

    # Listing shows only active versions (old one archived), plus the default checklist.
    listing = (await client.get(f"/portfolios/{pid}/checklists", headers=_auth(admin))).json()
    ids = {c["id"] for c in listing}
    assert created["id"] not in ids
    assert updated["id"] in ids


async def test_builder_requires_manage_role(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    viewer = await _login(client, "viewer@example.com")
    me = (await client.get("/me", headers=_auth(viewer))).json()
    await client.post(
        f"/portfolios/{pid}/members",
        json={"user_id": me["id"], "role": "VIEWER"},
        headers=_auth(admin),
    )
    # Viewer can read...
    read = await client.get(f"/portfolios/{pid}/checklists", headers=_auth(viewer))
    assert read.status_code == 200
    # ...but not create.
    denied = await client.post(
        f"/portfolios/{pid}/checklists", json={"name": "X", "items": [_ITEM]}, headers=_auth(viewer)
    )
    assert denied.status_code == 403
