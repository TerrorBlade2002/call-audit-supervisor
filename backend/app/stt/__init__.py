"""Speech-to-text: normalized transcript types + the AssemblyAI client (Phase 3).

The rest of the system depends only on the ``Transcript`` shape and the ``SttClient``
Protocol (in orchestration.stubs) — never on AssemblyAI specifics. That keeps the judge,
storage, and engine vendor-agnostic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Header AssemblyAI echoes back on the webhook for authenticity (§8.4). Set on submit.
WEBHOOK_AUTH_HEADER = "X-Everest-Webhook-Auth"

# Provider-neutral transcript lifecycle status.
TranscriptStatus = Literal["ready", "processing", "error"]


@dataclass(frozen=True)
class Utterance:
    speaker: str          # diarization label (agent vs consumer), e.g. "A"/"B"
    start_sec: float      # word/utterance start, seconds
    end_sec: float
    text: str


@dataclass(frozen=True)
class Transcript:
    """Vendor-neutral transcript: diarized utterances + flat text + duration."""

    transcript_id: str
    duration_sec: float | None
    text: str
    utterances: list[Utterance] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        return cls(
            transcript_id=data["transcript_id"],
            duration_sec=data.get("duration_sec"),
            text=data.get("text", ""),
            utterances=[Utterance(**u) for u in data.get("utterances", [])],
        )


__all__ = ["WEBHOOK_AUTH_HEADER", "Transcript", "TranscriptStatus", "Utterance"]
