"""Checklist item ORDER is the order they are sent in, plus JSON export/import round-trip.

The builder sends the complete desired state as an ordered list. It also echoes back each item
as it was loaded, which used to include the item's stored sort_order — so reordering with the
▲▼ controls and pressing Save snapped the list back to its old order, and a newly added item
(carrying no sort_order) collided with an existing item's number. Position is now the only
thing that decides order; these tests pin that down.
"""

from __future__ import annotations

import io
import json

from httpx import AsyncClient


async def _login(client: AsyncClient, email: str, *, as_admin: bool = False) -> str:
    resp = await client.post("/auth/dev-login", json={"email": email, "as_admin": as_admin})
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin_portfolio(client: AsyncClient) -> tuple[str, str]:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = (await client.post("/portfolios", json={"name": "P"}, headers=_auth(admin))).json()["id"]
    return admin, str(pid)


def _item(text: str, **over: object) -> dict[str, object]:
    return {"section": "A", "text": text, "answer_type": "PASS_FAIL", **over}


async def _create(client: AsyncClient, admin: str, pid: str, items: list[dict[str, object]]) -> str:
    resp = await client.post(
        f"/portfolios/{pid}/checklists",
        json={"name": "Asset Recovery", "items": items},
        headers=_auth(admin),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _texts(client: AsyncClient, admin: str, pid: str, cid: str) -> list[str]:
    detail = (
        await client.get(f"/portfolios/{pid}/checklists/{cid}", headers=_auth(admin))
    ).json()
    return [i["text"] for i in detail["items"]]


# --- ordering -------------------------------------------------------------------------------
async def test_items_keep_the_order_they_were_sent_in(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    cid = await _create(client, admin, pid, [_item("one"), _item("two"), _item("three")])
    assert await _texts(client, admin, pid, cid) == ["one", "two", "three"]


async def test_a_stale_sort_order_cannot_override_list_position(client: AsyncClient) -> None:
    """The exact reported bug: reorder in the builder, save, and the old order comes back.

    Each item carries the sort_order it had *before* the move, deliberately reversed here.
    Position must win.
    """
    admin, pid = await _admin_portfolio(client)
    items = [
        _item("first", sort_order=99),
        _item("second", sort_order=5),
        _item("third", sort_order=0),
    ]
    cid = await _create(client, admin, pid, items)
    assert await _texts(client, admin, pid, cid) == ["first", "second", "third"]


async def test_reordering_then_saving_persists_the_new_order(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    cid = await _create(client, admin, pid, [_item("a"), _item("b"), _item("c")])

    # Read them back the way the builder does, swap the first two (what ▲ does), and save.
    detail = (
        await client.get(f"/portfolios/{pid}/checklists/{cid}", headers=_auth(admin))
    ).json()
    loaded = detail["items"]
    loaded[0], loaded[1] = loaded[1], loaded[0]
    resp = await client.put(
        f"/portfolios/{pid}/checklists/{cid}",
        json={"name": detail["name"], "items": loaded},
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text
    assert [i["text"] for i in resp.json()["items"]] == ["b", "a", "c"]
    assert await _texts(client, admin, pid, cid) == ["b", "a", "c"]


async def test_new_item_inserted_mid_list_does_not_collide(client: AsyncClient) -> None:
    """A freshly added item has no sort_order; it must not land on top of an existing one."""
    admin, pid = await _admin_portfolio(client)
    cid = await _create(client, admin, pid, [_item("a"), _item("b")])
    detail = (
        await client.get(f"/portfolios/{pid}/checklists/{cid}", headers=_auth(admin))
    ).json()
    loaded = detail["items"]
    loaded.insert(1, _item("inserted"))  # no sort_order, mid-list
    resp = await client.put(
        f"/portfolios/{pid}/checklists/{cid}",
        json={"name": detail["name"], "items": loaded},
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text
    assert await _texts(client, admin, pid, cid) == ["a", "inserted", "b"]


# --- JSON export / import -------------------------------------------------------------------
async def test_export_json_round_trips_back_through_import(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)
    items = [
        _item("Executive summary", answer_type="TEXT", is_subjective=True),
        _item(
            "Did the collector verify the debtor?",
            answer_type="CHOICE",
            options=["Yes", "No", "NA"],
            risk="CRITICAL",
            guidance="Full name + two secondary identifiers.",
        ),
        _item("Did the collector identify the company?"),
    ]
    cid = await _create(client, admin, pid, items)

    exported = await client.get(
        f"/portfolios/{pid}/checklists/{cid}/export.json", headers=_auth(admin)
    )
    assert exported.status_code == 200, exported.text
    assert "attachment" in exported.headers["content-disposition"]
    payload = json.loads(exported.text)
    assert payload["name"] == "Asset Recovery"
    assert [i["text"] for i in payload["items"]] == [i["text"] for i in items]
    # Portable definition: no database identity leaks into the file.
    assert all("id" not in i and "sort_order" not in i for i in payload["items"])

    # Feed the very same bytes back in.
    resp = await client.post(
        f"/portfolios/{pid}/checklists/import-json",
        files={"file": ("asset.json", io.BytesIO(exported.content), "application/json")},
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text
    back = resp.json()
    assert back["name"] == "Asset Recovery"
    assert [i["text"] for i in back["items"]] == [i["text"] for i in items]
    assert back["items"][1]["options"] == ["Yes", "No", "NA"]
    assert back["items"][1]["risk"] == "CRITICAL"


async def test_import_rejects_bad_json_with_a_useful_message(client: AsyncClient) -> None:
    admin, pid = await _admin_portfolio(client)

    broken = await client.post(
        f"/portfolios/{pid}/checklists/import-json",
        files={"file": ("x.json", io.BytesIO(b"{not json"), "application/json")},
        headers=_auth(admin),
    )
    assert broken.status_code == 422
    assert "JSON" in broken.json()["detail"]

    # Valid JSON, wrong shape — the message must name the offending field.
    wrong = await client.post(
        f"/portfolios/{pid}/checklists/import-json",
        files={
            "file": (
                "x.json",
                io.BytesIO(json.dumps({"name": "N", "items": [{"text": "no section"}]}).encode()),
                "application/json",
            )
        },
        headers=_auth(admin),
    )
    assert wrong.status_code == 422
    assert "section" in wrong.json()["detail"]

    not_json_file = await client.post(
        f"/portfolios/{pid}/checklists/import-json",
        files={"file": ("x.txt", io.BytesIO(b"{}"), "text/plain")},
        headers=_auth(admin),
    )
    assert not_json_file.status_code == 422
    assert ".json" in not_json_file.json()["detail"]
