"""PII redaction is requested on every transcription, and unredacted text is never stored.

The guarantee has two halves and both are tested here:
  1. the request we send actually redacts (the provider redacts NOTHING when `redact_pii`
     is set without policies, so an empty policy list is a silent data leak, not a no-op);
  2. anything that comes back unredacted is refused rather than written to object storage.

Offline via httpx.MockTransport — no network, no provider spend.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.config import RateLimitSettings
from app.ratelimit.backoff import FatalError
from app.stt import Transcript
from app.stt.assemblyai import AssemblyAIClient
from app.stt.redaction import DEFAULT_PII_POLICIES, DEFAULT_SPEECH_MODELS, parse_csv_setting

_FAST = RateLimitSettings(RETRY_BASE_SECONDS=0.0, RETRY_CAP_SECONDS=0.0, RETRY_MAX_ATTEMPTS=1)


def _client(handler: httpx.MockTransport, **kw: object) -> AssemblyAIClient:
    http = httpx.AsyncClient(transport=handler, base_url="https://api.assemblyai.com")
    return AssemblyAIClient(
        api_key="k", base_url="https://api.assemblyai.com", retry=_FAST, client=http, **kw
    )


def _capture() -> tuple[httpx.MockTransport, dict[str, object]]:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"id": "t-1", "status": "queued"})

    return httpx.MockTransport(handler), seen


# --- what we ask the provider for ----------------------------------------------------------
async def test_submit_requests_redaction_with_a_non_empty_policy_list() -> None:
    transport, seen = _capture()
    await _client(transport).submit(call_id=uuid.uuid4(), audio_url="https://r2/x.wav")

    assert seen["redact_pii"] is True
    policies = seen["redact_pii_policies"]
    assert isinstance(policies, list) and policies, "empty policies redact NOTHING"
    assert seen["redact_pii_sub"] == "entity_name"
    # Redaction is only applied to formatted text, so this must be on for it to take effect.
    assert seen["format_text"] is True


async def test_submit_pins_models_that_support_redaction() -> None:
    """The provider's account-level default varies by tier; redaction needs a Universal model."""
    transport, seen = _capture()
    await _client(transport).submit(call_id=uuid.uuid4(), audio_url="https://r2/x.wav")
    assert seen["speech_models"] == list(DEFAULT_SPEECH_MODELS)


async def test_unredacted_transcript_is_never_requested() -> None:
    """Opting into the original text would put raw PII in this process; we never ask for it."""
    transport, seen = _capture()
    await _client(transport).submit(call_id=uuid.uuid4(), audio_url="https://r2/x.wav")
    assert "redact_pii_return_unredacted" not in seen


async def test_redaction_can_be_turned_off_explicitly() -> None:
    transport, seen = _capture()
    await _client(transport, redact_pii=False).submit(
        call_id=uuid.uuid4(), audio_url="https://r2/x.wav"
    )
    assert "redact_pii" not in seen


async def test_empty_policy_override_falls_back_to_defaults() -> None:
    """An empty list must not reach the provider — there it means 'redact nothing'."""
    transport, seen = _capture()
    await _client(transport, redact_pii_policies=[]).submit(
        call_id=uuid.uuid4(), audio_url="https://r2/x.wav"
    )
    assert seen["redact_pii_policies"] == list(DEFAULT_PII_POLICIES)
    assert parse_csv_setting("   ", DEFAULT_SPEECH_MODELS) == list(DEFAULT_SPEECH_MODELS)


# --- what we accept back ---------------------------------------------------------------------
def _fetch_transport(payload: dict[str, object]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda req: httpx.Response(200, json=payload))


async def test_fetch_rejects_a_transcript_that_was_not_redacted() -> None:
    """The provider echoes the flag back; if it is absent the text is raw, so refuse it."""
    transport = _fetch_transport(
        {"status": "completed", "text": "my name is Darryl Sanders", "utterances": []}
    )
    with pytest.raises(FatalError, match="WITHOUT PII redaction"):
        await _client(transport).fetch_transcript(transcript_id="t-1")


async def test_fetch_marks_a_redacted_transcript() -> None:
    transport = _fetch_transport(
        {
            "status": "completed",
            "redact_pii": True,
            "text": "my name is [PERSON_NAME]",
            "audio_duration": 12,
            "utterances": [{"speaker": "A", "start": 0, "end": 1000, "text": "[PERSON_NAME]"}],
        }
    )
    t = await _client(transport).fetch_transcript(transcript_id="t-1")
    assert t.redacted is True
    assert t.text == "my name is [PERSON_NAME]"
    assert t.utterances[0].text == "[PERSON_NAME]"


async def test_unredacted_is_allowed_only_when_redaction_is_off() -> None:
    transport = _fetch_transport({"status": "completed", "text": "raw", "utterances": []})
    t = await _client(transport, redact_pii=False).fetch_transcript(transcript_id="t-1")
    assert t.redacted is False


# --- the flag survives storage ----------------------------------------------------------------
def test_redacted_flag_round_trips_through_serialization() -> None:
    """Stored transcripts carry the flag, so a re-read object can still be told apart."""
    t = Transcript(transcript_id="t", duration_sec=1.0, text="[PERSON_NAME]", redacted=True)
    assert Transcript.from_dict(t.to_dict()).redacted is True
    # A legacy object written before the flag existed must read back as NOT redacted.
    assert Transcript.from_dict({"transcript_id": "t", "text": "x"}).redacted is False


# --- the policy set actually covers what the business asked for -------------------------------
@pytest.mark.parametrize(
    "policy",
    [
        "person_name",               # consumer full name
        "date_of_birth",
        "us_social_security_number",
        "phone_number",
        "email_address",
        "credit_card_number",
        "credit_card_cvv",
        "credit_card_expiration",
        "banking_information",       # debt / financial information
        "account_number",
        "money_amount",
        "organization",              # creditor / company names
        "location_address",
    ],
)
def test_required_pii_categories_are_covered(policy: str) -> None:
    assert policy in DEFAULT_PII_POLICIES


def test_occupation_is_excluded_to_preserve_the_mini_miranda() -> None:
    """Measured on real calls: redacting occupation removes the words 'debt collector',
    destroying the FDCPA disclosure the compliance checklist exists to verify."""
    assert "occupation" not in DEFAULT_PII_POLICIES

# The storage-step guard is driven by the portfolio's pii_mode, so it is covered end-to-end
# in test_portfolio_pii_mode.py (against a real portfolio row) rather than duplicated here.
