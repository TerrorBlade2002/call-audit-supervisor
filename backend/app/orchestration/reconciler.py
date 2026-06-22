"""Reconciler — the liveness guarantee (§8.5).

Makes a lost AssemblyAI webhook a non-event: any parked transcript whose webhook is
overdue is polled directly and advanced (or failed). Also resets the overdue window for
transcripts still processing so we don't hammer the provider. Runs on an interval in the
worker. Combined with the lease-based crash recovery in ``claim``, this is what
guarantees every job reaches a terminal state (NFR4).
"""

from __future__ import annotations

import re

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestration import queue
from app.orchestration.stubs import SttClient

log = structlog.get_logger("orchestration.reconciler")


def friendly_stt_error(raw: str | None) -> str:
    """Turn a provider error string into a clear, user-facing reason (no URLs/stack traces)."""
    if not raw:
        return "Transcription failed at the provider."
    low = raw.lower()
    if "download" in low or "not accessible" in low or "does not exist" in low:
        return (
            "Couldn't download the audio for transcription — the recording may not have "
            "finished uploading or isn't reachable."
        )
    if "does not appear to contain audio" in low or "no audio" in low:
        return "The file doesn't appear to contain valid audio."
    if "duration" in low and ("exceed" in low or "longer" in low):
        return "The recording is longer than the transcription limit."
    cleaned = re.sub(r"https?://\S+", "the audio file", raw).strip()
    return f"Transcription failed: {cleaned[:180]}"


async def run_once(
    session: AsyncSession, *, stt: SttClient, overdue_seconds: int
) -> int:
    """Sweep overdue parked transcripts once. Returns the number recovered/advanced."""
    overdue = await queue.find_overdue_awaiting(session, overdue_seconds=overdue_seconds)
    recovered = 0
    for job_id, transcript_id in overdue:
        if transcript_id is None:
            # No transcript id but parked — anomaly; fail it rather than strand it.
            await queue.mark_failed(session, job_id, reason="parked without transcript_id")
            continue

        status = await stt.poll(transcript_id=transcript_id)
        if status == "ready":
            advanced = await queue.advance_to_judge_by_transcript(session, transcript_id)
            if advanced:
                recovered += 1
                log.info("reconciler.recovered", job_id=str(job_id), transcript_id=transcript_id)
        elif status == "error":
            detail = await stt.get_error(transcript_id=transcript_id)
            await queue.mark_failed(session, job_id, reason=friendly_stt_error(detail))
        else:  # processing — extend the window, try again next sweep
            await queue.touch(session, job_id)
    return recovered
