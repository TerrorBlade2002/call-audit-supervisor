"""calls.original_filename — the uploader's file name is captured and surfaced (migration 0014).

Recording object keys are random UUIDs, so the name the user uploaded is the only human-readable
link from a report back to its source audio. Two concerns are covered here: the sanitizing that
happens at the trust boundary (the name is client-supplied and ends up in HTML + CSV), and the
round trip through registration → CallOut → ReportOut.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api import csv_safe
from app.api.calls import _clean_filename
from app.config import settings


@pytest.fixture(autouse=True)
def _dummy_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "r2_endpoint_url", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setattr(settings, "r2_access_key_id", "test-key")
    monkeypatch.setattr(settings, "r2_secret_access_key", "test-secret")


async def _login(client: AsyncClient, email: str, *, as_admin: bool = False) -> str:
    resp = await client.post("/auth/dev-login", json={"email": email, "as_admin": as_admin})
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _portfolio_and_agent(client: AsyncClient, admin: str) -> tuple[str, str]:
    pid = (await client.post("/portfolios", json={"name": "P"}, headers=_auth(admin))).json()["id"]
    aid = (
        await client.post(
            f"/portfolios/{pid}/agents", json={"name": "Agent A"}, headers=_auth(admin)
        )
    ).json()["id"]
    return str(pid), str(aid)


# --- sanitizing at the trust boundary -------------------------------------------------------
def test_clean_filename_keeps_a_plain_name() -> None:
    assert _clean_filename("AGT-1042_2026-07-28.mp3") == "AGT-1042_2026-07-28.mp3"


def test_clean_filename_strips_directory_components() -> None:
    """A folder upload sends a relative path; only the basename identifies the recording."""
    assert _clean_filename("july/batch2/call.mp3") == "call.mp3"
    assert _clean_filename(r"C:\Users\me\Desktop\call.mp3") == "call.mp3"


def test_clean_filename_strips_control_characters() -> None:
    """A newline would break the CSV row it is written into."""
    assert _clean_filename("call\r\n injected.mp3") == "call injected.mp3"


@pytest.mark.parametrize("raw", [None, "", "   ", "\x00\x01"])
def test_clean_filename_returns_none_when_empty(raw: str | None) -> None:
    """Nothing usable in, nothing stored — the call falls back to its short id in the UI."""
    assert _clean_filename(raw) is None


def test_clean_filename_truncates_to_column_width() -> None:
    assert len(_clean_filename("a" * 900) or "") == 400


@pytest.mark.parametrize("lead", ["=", "+", "-", "@"])
def test_csv_safe_neutralizes_formula_leads(lead: str) -> None:
    """A spreadsheet evaluates a cell starting with these; a file can legitimately be named so."""
    assert csv_safe(f"{lead}call.mp3") == f"'{lead}call.mp3"


def test_csv_safe_passes_ordinary_values_through() -> None:
    assert csv_safe("call.mp3") == "call.mp3"
    assert csv_safe(None) is None


# --- round trip through the API -------------------------------------------------------------
async def test_registered_call_keeps_the_original_filename(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid, aid = await _portfolio_and_agent(client, admin)

    resp = await client.post(
        f"/portfolios/{pid}/agents/{aid}/calls",
        json={
            "items": [
                {"key": f"{pid}/{aid}/abc.mp3", "filename": "uploads/AGT-1042 call.mp3"},
                {"key": f"{pid}/{aid}/def.mp3"},  # older client: no name sent
            ]
        },
        headers=_auth(admin),
    )
    assert resp.status_code == 201, resp.text
    calls = resp.json()["calls"]
    # Compared as a set: both rows are inserted in one transaction and ordered by created_at,
    # so their relative order is not guaranteed. What matters is that the directory component
    # is dropped and that the call with no name sent stays null rather than inventing one.
    assert {c["original_filename"] for c in calls} == {"AGT-1042 call.mp3", None}

    listing = await client.get(f"/portfolios/{pid}/agents/{aid}/calls", headers=_auth(admin))
    assert {c["original_filename"] for c in listing.json()} == {"AGT-1042 call.mp3", None}


async def test_separate_registrations_can_share_one_batch(client: AsyncClient) -> None:
    """Recordings upload one request per file, so they must be able to join an open batch.

    Without this the batch summary and the batch CSV would see each file as its own batch.
    """
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid, aid = await _portfolio_and_agent(client, admin)

    async def register(key: str, batch: str | None) -> dict[str, object]:
        items: dict[str, object] = {"items": [{"key": f"{pid}/{aid}/{key}", "filename": key}]}
        if batch is not None:
            items["batch_id"] = batch
        resp = await client.post(
            f"/portfolios/{pid}/agents/{aid}/calls", json=items, headers=_auth(admin)
        )
        assert resp.status_code == 201, resp.text
        return dict(resp.json())

    first = await register("one.mp3", None)
    batch = str(first["batch_id"])
    second = await register("two.mp3", batch)

    # The second registration joined the first one's batch instead of opening a new one.
    assert str(second["batch_id"]) == batch
    listing = await client.get(f"/portfolios/{pid}/agents/{aid}/calls", headers=_auth(admin))
    assert {c["batch_id"] for c in listing.json()} == {batch}
