"""Per-portfolio PII redaction level: OFF / TRANSCRIPT / TRANSCRIPT_AND_RECORDING.

The level is admin-set and read LIVE when a call is processed — never snapshotted onto the
call — so changing it governs every call processed afterwards. These tests pin the three
behaviours that follow: what we ask the provider for, what we refuse to store, and whether
the stored recording gets replaced.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.db import session_scope
from app.orchestration.handlers import JobView, handle_judge, handle_transcription
from app.ratelimit import build_limiters
from app.ratelimit.backoff import FatalError
from app.ratelimit.caps import DailyCap, InMemoryDailyUsageStore
from app.storage import FakeStorage
from app.stt import Transcript
from app.stt.redaction import PiiMode, parse_pii_mode


# --- the mode itself ------------------------------------------------------------------------
def test_levels_describe_what_they_redact() -> None:
    assert not PiiMode.OFF.redacts_transcript and not PiiMode.OFF.redacts_recording
    assert PiiMode.TRANSCRIPT.redacts_transcript and not PiiMode.TRANSCRIPT.redacts_recording
    both = PiiMode.TRANSCRIPT_AND_RECORDING
    assert both.redacts_transcript and both.redacts_recording


@pytest.mark.parametrize("raw", [None, "", "nonsense", "transcript_only"])
def test_unknown_values_fall_back_to_off(raw: str | None) -> None:
    """A value written by another version must never silently enable redaction."""
    assert parse_pii_mode(raw) is PiiMode.OFF


def test_parsing_is_case_insensitive() -> None:
    assert parse_pii_mode("transcript") is PiiMode.TRANSCRIPT


# --- the admin API --------------------------------------------------------------------------
async def _login(client: AsyncClient, email: str, *, as_admin: bool = False) -> str:
    resp = await client.post("/auth/dev-login", json={"email": email, "as_admin": as_admin})
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_portfolio(client: AsyncClient, admin: str) -> str:
    resp = await client.post("/portfolios", json={"name": "P"}, headers=_auth(admin))
    return str(resp.json()["id"])


async def test_new_portfolios_default_to_off(client: AsyncClient) -> None:
    """Redaction is an explicit per-client decision — it never switches itself on."""
    admin = await _login(client, "admin@example.com", as_admin=True)
    created = await client.post("/portfolios", json={"name": "P"}, headers=_auth(admin))
    assert created.json()["pii_mode"] == "OFF"


async def test_admin_can_change_the_level(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = await _make_portfolio(client, admin)

    resp = await client.patch(
        f"/portfolios/{pid}/pii-mode",
        json={"pii_mode": "TRANSCRIPT_AND_RECORDING"},
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pii_mode"] == "TRANSCRIPT_AND_RECORDING"

    listing = (await client.get("/portfolios", headers=_auth(admin))).json()
    assert [p["pii_mode"] for p in listing if p["id"] == pid] == ["TRANSCRIPT_AND_RECORDING"]


async def test_non_admin_cannot_change_the_level(client: AsyncClient) -> None:
    """A compliance control sits behind the super-admin gate, not the supervisor one."""
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = await _make_portfolio(client, admin)
    supervisor = await _login(client, "sup@example.com")

    resp = await client.patch(
        f"/portfolios/{pid}/pii-mode", json={"pii_mode": "OFF"}, headers=_auth(supervisor)
    )
    assert resp.status_code in (401, 403), resp.text


async def test_an_invalid_level_is_rejected(client: AsyncClient) -> None:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = await _make_portfolio(client, admin)
    resp = await client.patch(
        f"/portfolios/{pid}/pii-mode", json={"pii_mode": "SOMETIMES"}, headers=_auth(admin)
    )
    assert resp.status_code == 422


# --- what the level does to processing ---------------------------------------------------------
class _RecordingStt:
    """Captures the per-call redaction flags and whether redacted audio was asked for."""

    def __init__(self, *, redacted: bool = True) -> None:
        self.submitted: dict[str, object] = {}
        self.redacted_audio_requested = False
        self._redacted = redacted

    async def submit(
        self,
        *,
        call_id: uuid.UUID,
        audio_url: str,
        redact_pii: bool | None = None,
        redact_pii_audio: bool = False,
    ) -> str:
        self.submitted = {"redact_pii": redact_pii, "redact_pii_audio": redact_pii_audio}
        return "t-1"

    async def poll(self, *, transcript_id: str) -> str:
        return "ready"

    async def get_error(self, *, transcript_id: str) -> str | None:
        return None

    async def fetch_transcript(
        self, *, transcript_id: str, require_redacted: bool | None = None
    ) -> Transcript:
        return Transcript(
            transcript_id=transcript_id,
            duration_sec=1.0,
            text="[PERSON_NAME]" if self._redacted else "Darryl Sanders",
            redacted=self._redacted,
        )

    async def get_redacted_audio_url(self, *, transcript_id: str) -> str | None:
        self.redacted_audio_requested = True
        return None  # nothing to download in a unit test; the call must still complete


async def _portfolio_with_mode(client: AsyncClient, mode: str) -> str:
    admin = await _login(client, "admin@example.com", as_admin=True)
    pid = await _make_portfolio(client, admin)
    await client.patch(
        f"/portfolios/{pid}/pii-mode", json={"pii_mode": mode}, headers=_auth(admin)
    )
    return pid


def _job(pid: str, *, transcript_id: str | None) -> JobView:
    return JobView(
        id=uuid.uuid4(),
        call_id=uuid.uuid4(),
        portfolio_id=uuid.UUID(pid),
        transcript_id=transcript_id,
        audio_uri="p/a/rec.mp3",
        transcript_uri=None,
    )


@pytest.mark.parametrize(
    ("mode", "expect_redact", "expect_audio"),
    [
        ("OFF", False, False),
        ("TRANSCRIPT", True, False),
        ("TRANSCRIPT_AND_RECORDING", True, True),
    ],
)
async def test_submit_flags_follow_the_portfolio_level(
    client: AsyncClient, mode: str, expect_redact: bool, expect_audio: bool
) -> None:
    """redact_pii_audio must be asked for at submit time — it cannot be added afterwards."""
    pid = await _portfolio_with_mode(client, mode)
    storage = FakeStorage()
    storage.recordings["p/a/rec.mp3"] = b"audio"
    stt = _RecordingStt()
    settings = get_settings()
    async with session_scope() as session:
        await handle_transcription(
            _job(pid, transcript_id=None),
            session=session,
            limiters=build_limiters(settings.ratelimit),
            daily_cap=DailyCap(InMemoryDailyUsageStore(), cap=1000),
            stt=stt,
            storage=storage,
        )
    assert stt.submitted == {"redact_pii": expect_redact, "redact_pii_audio": expect_audio}


async def test_an_off_portfolio_stores_an_unredacted_transcript(client: AsyncClient) -> None:
    """The storage guard keys off the portfolio level, not a global flag."""
    pid = await _portfolio_with_mode(client, "OFF")
    storage = FakeStorage()
    settings = get_settings()
    async with session_scope() as session:
        # Judging then fails for an unrelated reason (no agents are wired up in this unit
        # test). That is incidental: it happens AFTER the transcript is stored, and the
        # storing is what is under test. Matched specifically so a DIFFERENT failure — such
        # as the redaction guard wrongly tripping — would fail this test rather than pass it.
        with pytest.raises(FatalError, match="requires the merged"):
            await handle_judge(
                _job(pid, transcript_id="t-1"),
                session=session,
                limiters=build_limiters(settings.ratelimit),
                stt=_RecordingStt(redacted=False),
                storage=storage,
                judge=None,
                embedder=None,
                routing_config=None,
            )
    assert storage.transcripts, "an OFF portfolio must not be blocked by the redaction guard"


async def test_a_redacting_portfolio_refuses_an_unredacted_transcript(
    client: AsyncClient,
) -> None:
    pid = await _portfolio_with_mode(client, "TRANSCRIPT")
    storage = FakeStorage()
    settings = get_settings()
    async with session_scope() as session:
        with pytest.raises(FatalError, match="refusing to store an unredacted transcript"):
            await handle_judge(
                _job(pid, transcript_id="t-1"),
                session=session,
                limiters=build_limiters(settings.ratelimit),
                stt=_RecordingStt(redacted=False),
                storage=storage,
                judge=None,
                embedder=None,
                routing_config=None,
            )
    assert storage.transcripts == {}, "nothing may be written when the guard trips"
