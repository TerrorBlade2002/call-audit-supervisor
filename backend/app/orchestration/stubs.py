"""STT stub for the orchestration engine (§18).

The ``SttClient`` Protocol + ``StubStt`` let the engine be proven without AssemblyAI; the
real client (app.stt.assemblyai) drops in unchanged. The judge Protocol + stub live in
app.judge.client (Phase 5). Stubs are configurable to fail N times to exercise retries.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.ratelimit.backoff import RetryableError
from app.stt import Transcript, TranscriptStatus, Utterance


class SttClient(Protocol):
    async def submit(self, *, call_id: uuid.UUID, audio_url: str) -> str:
        """Submit audio (a downloadable URL) for async transcription; return a transcript id."""
        ...

    async def poll(self, *, transcript_id: str) -> TranscriptStatus:
        """Reconciler fallback: ask the provider for a transcript's status (§8.5)."""
        ...

    async def get_error(self, *, transcript_id: str) -> str | None:
        """Provider-reported failure reason for an errored transcript (for a graceful
        user-facing message). None if unavailable."""
        ...

    async def fetch_transcript(self, *, transcript_id: str) -> Transcript:
        """Fetch the completed, diarized transcript for storage + judging."""
        ...


class StubStt:
    """Returns a deterministic fake transcript id. Optionally fails the first N calls."""

    def __init__(self, fail_times: int = 0, poll_status: TranscriptStatus = "ready") -> None:
        self._remaining_failures = fail_times
        self._poll_status = poll_status

    async def submit(self, *, call_id: uuid.UUID, audio_url: str) -> str:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RetryableError("stub STT transient failure")
        return f"stub-transcript-{call_id}"

    async def poll(self, *, transcript_id: str) -> TranscriptStatus:
        return self._poll_status

    async def get_error(self, *, transcript_id: str) -> str | None:
        return None

    async def fetch_transcript(self, *, transcript_id: str) -> Transcript:
        return Transcript(
            transcript_id=transcript_id,
            duration_sec=300.0,
            text="Agent: Hello. Consumer: Hi.",
            utterances=[
                Utterance(speaker="A", start_sec=0.0, end_sec=2.0, text="Hello."),
                Utterance(speaker="B", start_sec=2.0, end_sec=4.0, text="Hi."),
            ],
        )
