"""AssemblyAI client (Phase 3). Offline via httpx.MockTransport — no network."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.config import RateLimitSettings
from app.stt import WEBHOOK_AUTH_HEADER
from app.stt.assemblyai import AssemblyAIClient

# Fast retries (no real sleeping) for the 429 test.
_FAST = RateLimitSettings(RETRY_BASE_SECONDS=0.0, RETRY_CAP_SECONDS=0.0, RETRY_MAX_ATTEMPTS=3)


def _client(handler: httpx.MockTransport, **kw: object) -> AssemblyAIClient:
    http = httpx.AsyncClient(transport=handler, base_url="https://api.assemblyai.com")
    return AssemblyAIClient(
        api_key="k", base_url="https://api.assemblyai.com", retry=_FAST, client=http, **kw
    )


async def test_submit_requests_diarization_and_webhook_auth() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"id": "t-123", "status": "queued"})

    client = _client(
        httpx.MockTransport(handler),
        webhook_url="https://app.example/webhooks/assemblyai",
        webhook_auth_header=WEBHOOK_AUTH_HEADER,
        webhook_secret="s3cr3t",
    )
    tid = await client.submit(call_id=uuid.uuid4(), audio_url="https://r2/x.wav")
    assert tid == "t-123"
    assert seen["speaker_labels"] is True              # diarization requested (FR7)
    assert seen["audio_url"] == "https://r2/x.wav"
    assert seen["webhook_url"] == "https://app.example/webhooks/assemblyai"
    assert seen["webhook_auth_header_name"] == WEBHOOK_AUTH_HEADER
    assert seen["webhook_auth_header_value"] == "s3cr3t"


@pytest.mark.parametrize(
    "aai_status,expected",
    [
        ("completed", "ready"),
        ("queued", "processing"),
        ("processing", "processing"),
        ("error", "error"),
    ],
)
async def test_poll_maps_status(aai_status: str, expected: str) -> None:
    client = _client(
        httpx.MockTransport(lambda req: httpx.Response(200, json={"status": aai_status}))
    )
    assert await client.poll(transcript_id="t-1") == expected


async def test_fetch_transcript_normalizes_diarization() -> None:
    payload = {
        "status": "completed",
        "text": "Hello. Hi.",
        "audio_duration": 300,
        "utterances": [
            {"speaker": "A", "start": 0, "end": 2000, "text": "Hello."},
            {"speaker": "B", "start": 2000, "end": 4000, "text": "Hi."},
        ],
    }
    client = _client(httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    t = await client.fetch_transcript(transcript_id="t-1")
    assert t.duration_sec == 300.0
    assert len(t.utterances) == 2
    assert t.utterances[0].speaker == "A"
    assert t.utterances[0].start_sec == 0.0
    assert t.utterances[1].end_sec == 4.0  # ms -> sec


async def test_retries_on_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={})
        return httpx.Response(200, json={"id": "t-9", "status": "queued"})

    client = _client(httpx.MockTransport(handler))
    tid = await client.submit(call_id=uuid.uuid4(), audio_url="https://r2/x.wav")
    assert tid == "t-9"
    assert calls["n"] == 2  # retried once


async def test_worker_stt_configures_webhook_when_public_url_and_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import main as worker_main

    monkeypatch.setattr(worker_main.settings, "assemblyai_api_key", "k")
    monkeypatch.setattr(worker_main.settings, "public_base_url", "https://api.example.com/")
    monkeypatch.setattr(worker_main.settings, "assemblyai_webhook_secret", "secret")
    http = httpx.AsyncClient()
    try:
        stt = worker_main._build_stt(http)
        assert isinstance(stt, AssemblyAIClient)
        assert stt._webhook_url == "https://api.example.com/webhooks/assemblyai"
        assert stt._webhook_secret == "secret"
    finally:
        await http.aclose()


async def test_worker_stt_uses_polling_when_webhook_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import main as worker_main

    monkeypatch.setattr(worker_main.settings, "assemblyai_api_key", "k")
    monkeypatch.setattr(worker_main.settings, "public_base_url", "https://api.example.com")
    monkeypatch.setattr(worker_main.settings, "assemblyai_webhook_secret", "")
    http = httpx.AsyncClient()
    try:
        stt = worker_main._build_stt(http)
        assert isinstance(stt, AssemblyAIClient)
        assert stt._webhook_url is None
    finally:
        await http.aclose()
