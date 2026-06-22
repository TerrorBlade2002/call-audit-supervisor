"""Raw STT transcripts: an append-only log + per-call .txt download (§ output features).

Transcripts live in the R2 transcripts bucket; this exposes the append-only index (call id +
folder + upload time) and serves each transcript as plain text on demand. No in-app preview.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import AuthContext, authorize
from app.config import settings
from app.db import get_session
from app.models import Agent, Call
from app.rbac import Action
from app.schemas import TranscriptLogOut
from app.storage import build_storage
from app.stt import Transcript

router = APIRouter(tags=["transcripts"])


def _transcript_to_text(t: Transcript) -> str:
    if t.utterances:
        return "\n".join(f"[{u.speaker} @ {u.start_sec:.0f}s] {u.text}" for u in t.utterances)
    return t.text


@router.get("/portfolios/{pid}/transcripts", response_model=list[TranscriptLogOut])
async def list_transcripts(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TranscriptLogOut]:
    """Append-only transcript index — every call that has a stored transcript, newest first."""
    rows = (
        await session.execute(
            select(Call.id, Agent.name, Call.created_at)
            .join(Agent, Agent.id == Call.agent_id, isouter=True)
            .where(Call.portfolio_id == pid, Call.r2_transcript_uri.isnot(None))
            .order_by(Call.created_at.desc())
        )
    ).all()
    return [TranscriptLogOut(call_id=c, agent_name=a, created_at=t) for c, a, t in rows]


@router.get("/portfolios/{pid}/calls/{call_id}/transcript.txt")
async def download_transcript(
    pid: uuid.UUID,
    call_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.INSIGHTS_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """The raw diarized transcript as a .txt download (fetched from R2 on demand)."""
    call = await session.scalar(
        select(Call).where(Call.id == call_id, Call.portfolio_id == pid)
    )
    if call is None or call.r2_transcript_uri is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no transcript")
    transcript = await build_storage(settings).get_transcript(call_id)
    return Response(
        content=_transcript_to_text(transcript),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="transcript_{str(call_id)[:8]}.txt"'
        },
    )
