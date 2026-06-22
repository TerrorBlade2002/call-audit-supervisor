"""Verification endpoints (FR12, §10): submit judgement, download recording, view
transcript, and read the judge↔verifier agreement metric.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.authz import AuthContext, authorize, authorize_report
from app.config import settings
from app.db import get_session
from app.models import Call, Report
from app.rbac import Action
from app.schemas import (
    AgreementOut,
    DownloadUrlOut,
    VerificationCreate,
    VerificationOut,
)
from app.storage import StorageService, build_storage
from app.verification.service import agreement_stats, submit_verification

router = APIRouter(tags=["verification"])

# Module-level so tests can monkeypatch (R2 in prod, shared local-disk in dev).
_storage: StorageService = build_storage(settings)


async def _call_for_report(session: AsyncSession, report_id: uuid.UUID) -> Call:
    call = await session.scalar(
        select(Call).join(Report, Report.call_id == Call.id).where(Report.id == report_id)
    )
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    return call


@router.post(
    "/reports/{report_id}/verification",
    response_model=VerificationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_verification(
    report_id: uuid.UUID,
    body: VerificationCreate,
    ctx: Annotated[AuthContext, Depends(authorize_report(Action.VERIFICATION_SUBMIT))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VerificationOut:
    verification = await submit_verification(
        session, report_id=report_id, verifier_id=ctx.user.id,
        judgement=body.judgement, notes=body.notes,
    )
    await record_audit(
        session, actor_id=ctx.user.id, action="verification.submit",
        entity="report", entity_id=report_id, meta={"judgement": body.judgement},
    )
    await session.commit()
    return VerificationOut.model_validate(verification)


@router.get("/reports/{report_id}/recording:download", response_model=DownloadUrlOut)
async def download_recording(
    report_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize_report(Action.RECORDING_DOWNLOAD))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DownloadUrlOut:
    call = await _call_for_report(session, report_id)
    url = _storage.presign_audio_get(call.r2_audio_uri)  # short-TTL presigned GET (NFR6)
    return DownloadUrlOut(url=url, expires_in=settings.r2_presign_ttl_seconds)


@router.get("/reports/{report_id}/transcript")
async def get_transcript(
    report_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Diarized transcript inline (evidence deep-links to timestamps in the UI)."""
    call = await _call_for_report(session, report_id)
    transcript = await _storage.get_transcript(call.id)
    return transcript.to_dict()


@router.get("/portfolios/{pid}/verification-stats", response_model=AgreementOut)
async def verification_stats(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.REPORT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgreementOut:
    stats = await agreement_stats(session, pid)
    return AgreementOut.model_validate(stats)
