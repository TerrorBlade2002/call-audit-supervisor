"""AssemblyAI webhook receiver (§8.4, Phase 3).

Verifies the request is genuinely AssemblyAI via the shared-secret header we set on submit
(reject otherwise — untrusted input). On `completed`, advances AWAITING_TRANSCRIPT →
PENDING_JUDGE and returns 200 fast (no transcript fetch / judge inline — that's the
worker's job). Idempotent: a redelivered webhook for an already-advanced job is a no-op
200. The reconciler (§8.5) covers a webhook that never arrives.
"""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.notifier import PgNotifier
from app.orchestration import queue
from app.stt import WEBHOOK_AUTH_HEADER

router = APIRouter(tags=["webhooks"])
_notifier = PgNotifier()


def _verify(request: Request) -> None:
    secret = settings.assemblyai_webhook_secret
    provided = request.headers.get(WEBHOOK_AUTH_HEADER, "")
    # No configured secret → reject (fail closed); else constant-time compare.
    if not secret or not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unverified webhook")


@router.post("/webhooks/assemblyai")
async def assemblyai_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    _verify(request)

    body = await request.json()
    transcript_id = body.get("transcript_id") or body.get("id")
    status_str = body.get("status")
    if not transcript_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing transcript_id")

    if status_str == "completed":
        target = await _lookup(session, transcript_id)
        advanced = await queue.advance_to_judge_by_transcript(session, transcript_id)
        if advanced and target is not None:
            await _notifier.publish(
                session, portfolio_id=target[0], call_id=target[1], state="PENDING_JUDGE"
            )
        await session.commit()
    elif status_str == "error":
        await queue.fail_by_transcript(session, transcript_id, reason="AssemblyAI returned error")
        await session.commit()
    # Any other status (queued/processing) or redelivery → benign no-op.
    return {"ok": True}


async def _lookup(
    session: AsyncSession, transcript_id: str
) -> tuple[uuid.UUID, uuid.UUID] | None:
    row = (
        await session.execute(
            text("SELECT portfolio_id, call_id FROM jobs WHERE transcript_id = :tid"),
            {"tid": transcript_id},
        )
    ).first()
    return (row.portfolio_id, row.call_id) if row else None
