"""AssemblyAI async STT client (§7.1, Phase 3).

Submit-and-webhook (no inline polling): submit returns a ``transcript_id`` immediately; the
job parks until AssemblyAI calls our webhook (or the reconciler polls as a fallback).
Diarization (``speaker_labels``) + word timings are requested. Transient errors (429/5xx)
are retried with backoff; the ``httpx.AsyncClient`` is injectable so tests run offline.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import RateLimitSettings
from app.ratelimit.backoff import FatalError, classify_http_status, retry_async
from app.stt import Transcript, TranscriptStatus, Utterance
from app.stt.redaction import DEFAULT_PII_POLICIES, DEFAULT_PII_SUB, DEFAULT_SPEECH_MODELS

# AssemblyAI status string -> our neutral status.
_STATUS_MAP: dict[str, TranscriptStatus] = {
    "completed": "ready",
    "error": "error",
    "queued": "processing",
    "processing": "processing",
}


class AssemblyAIClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        retry: RateLimitSettings,
        client: httpx.AsyncClient,
        webhook_url: str | None = None,
        webhook_auth_header: str | None = None,
        webhook_secret: str | None = None,
        speech_models: list[str] | None = None,
        redact_pii: bool = True,
        redact_pii_policies: list[str] | None = None,
        redact_pii_sub: str = DEFAULT_PII_SUB,
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._retry = retry
        self._client = client
        self._webhook_url = webhook_url
        self._webhook_auth_header = webhook_auth_header
        self._webhook_secret = webhook_secret
        self._speech_models = speech_models or list(DEFAULT_SPEECH_MODELS)
        self._redact_pii = redact_pii
        # The provider redacts nothing when the policy list is empty, so an empty override is
        # treated as "use the defaults" rather than silently disabling redaction.
        self._redact_policies = list(redact_pii_policies or DEFAULT_PII_POLICIES)
        self._redact_sub = redact_pii_sub

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": self._key}

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async def _do() -> dict[str, Any]:
            resp = await self._client.request(
                method, f"{self._base}{path}", headers=self._headers, json=json
            )
            retry_after = resp.headers.get("retry-after")
            classify_http_status(
                resp.status_code, float(retry_after) if retry_after else None
            )
            return dict(resp.json())

        return await retry_async(
            _do,
            max_attempts=self._retry.retry_max_attempts,
            base=self._retry.retry_base_seconds,
            cap=self._retry.retry_cap_seconds,
            jitter_ratio=self._retry.retry_jitter_ratio,
        )

    async def submit(self, *, call_id: object, audio_url: str) -> str:
        """Submit audio for async, diarized, PII-redacted transcription → transcript id."""
        body: dict[str, object] = {
            "audio_url": audio_url,
            "speaker_labels": True,
            "speech_models": self._speech_models,
            # Redaction requires formatted text; both are the provider's own defaults but are
            # sent explicitly so a future default change cannot silently disable redaction.
            "format_text": True,
            "punctuate": True,
        }
        if self._redact_pii:
            # policies are mandatory — `redact_pii: true` with no policies redacts NOTHING.
            body["redact_pii"] = True
            body["redact_pii_policies"] = self._redact_policies
            body["redact_pii_sub"] = self._redact_sub
            # `redact_pii_return_unredacted` is deliberately NOT sent. Left off, the provider
            # returns only redacted content, so the raw text never reaches this process and
            # therefore can never be written to storage or shown in the UI.
        if self._webhook_url:
            body["webhook_url"] = self._webhook_url
            if self._webhook_auth_header and self._webhook_secret:
                body["webhook_auth_header_name"] = self._webhook_auth_header
                body["webhook_auth_header_value"] = self._webhook_secret
        data = await self._request("POST", "/v2/transcript", json=body)
        return str(data["id"])

    async def poll(self, *, transcript_id: str) -> TranscriptStatus:
        data = await self._request("GET", f"/v2/transcript/{transcript_id}")
        return _STATUS_MAP.get(str(data.get("status")), "processing")

    async def get_error(self, *, transcript_id: str) -> str | None:
        """The provider's ``error`` field for a failed transcript (e.g. a download error)."""
        data = await self._request("GET", f"/v2/transcript/{transcript_id}")
        err = data.get("error")
        return str(err) if err else None

    async def fetch_transcript(self, *, transcript_id: str) -> Transcript:
        """Fetch the completed transcript and normalize to our diarized shape.

        Refuses to return a transcript that is not redacted while redaction is enabled. The
        caller stores whatever this returns, so this is the last point at which unredacted
        text can be stopped — failing the job is strictly better than persisting PII. It is a
        FatalError because a retry would re-fetch the same already-transcribed audio and fail
        identically; the call needs re-submitting, not retrying.
        """
        data = await self._request("GET", f"/v2/transcript/{transcript_id}")
        if self._redact_pii and not bool(data.get("redact_pii")):
            raise FatalError(
                f"transcript {transcript_id} came back WITHOUT PII redaction applied "
                "(redact_pii is not set on the provider's response) — refusing to store it"
            )
        utterances = [
            Utterance(
                speaker=str(u.get("speaker", "?")),
                start_sec=float(u.get("start", 0)) / 1000.0,  # AAI gives milliseconds
                end_sec=float(u.get("end", 0)) / 1000.0,
                text=str(u.get("text", "")),
            )
            for u in (data.get("utterances") or [])
        ]
        duration = data.get("audio_duration")
        return Transcript(
            transcript_id=transcript_id,
            duration_sec=float(duration) if duration is not None else None,
            text=str(data.get("text") or ""),
            utterances=utterances,
            redacted=bool(data.get("redact_pii")),
        )
