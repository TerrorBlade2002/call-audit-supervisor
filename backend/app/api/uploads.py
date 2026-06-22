"""Presigned upload issuance (FR3.1/FR3.2). Browser uploads straight to R2 — never proxied.

Two-step ingestion: (1) POST .../uploads:presign returns presigned PUT URLs + the object
keys; the browser PUTs each file to R2; (2) POST .../calls registers the uploaded keys and
enqueues jobs (see api/calls.py). Keys are portfolio/agent-prefixed so registration can
authorize them by prefix.
"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.calls import assert_inflight_quota, register_keys
from app.authz import AuthContext, authorize
from app.config import settings
from app.db import get_session
from app.judge.options import ProcessingOption
from app.models import Agent
from app.rbac import Action
from app.schemas import CallRegisterResponse, PresignItem, PresignRequest, PresignResponse
from app.storage import build_s3_client, build_storage, presign_put, recording_key

router = APIRouter(tags=["uploads"])

MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 250 * 1024 * 1024  # 250 MB/file — generous for a call recording


def _audio_content_type(filename: str, declared: str | None) -> str:
    if declared and declared != "application/octet-stream":
        return declared
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def _require_agent(session: AsyncSession, pid: uuid.UUID, aid: uuid.UUID) -> Agent:
    agent = await session.scalar(select(Agent).where(Agent.id == aid, Agent.portfolio_id == pid))
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    return agent


def _parse_option(raw: str) -> ProcessingOption:
    try:
        return ProcessingOption(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"invalid option: {raw}"
        ) from exc


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid checklist id"
        ) from exc


def _parse_uuid_list(raw: str | None) -> list[uuid.UUID] | None:
    """Comma-separated KB doc ids → list (None/empty = the portfolio default doc set)."""
    if not raw:
        return None
    out: list[uuid.UUID] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(uuid.UUID(part))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid kb doc id: {part}",
            ) from exc
    return out or None


@router.post("/portfolios/{pid}/agents/{aid}/uploads:presign", response_model=PresignResponse)
async def presign_uploads(
    pid: uuid.UUID,
    aid: uuid.UUID,
    body: PresignRequest,
    ctx: Annotated[AuthContext, Depends(authorize(Action.RECORDING_UPLOAD))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PresignResponse:
    # The agent must belong to this portfolio (attribution is implicit, FR3.1).
    await _require_agent(session, pid, aid)

    client = build_s3_client(settings)
    bucket = settings.r2_bucket_recordings
    ttl = settings.r2_presign_ttl_seconds
    uploads = [
        PresignItem(
            filename=f.filename,
            key=(key := recording_key(pid, aid, f.filename)),
            upload_url=presign_put(client, bucket, key, ttl=ttl),
        )
        for f in body.files
    ]
    return PresignResponse(bucket=bucket, expires_in=ttl, uploads=uploads)


@router.post(
    "/portfolios/{pid}/agents/{aid}/recordings",
    response_model=CallRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_recordings(
    pid: uuid.UUID,
    aid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.RECORDING_UPLOAD))],
    session: Annotated[AsyncSession, Depends(get_session)],
    files: Annotated[list[UploadFile], File()],
    option: Annotated[str, Form()] = "FULL",
    checklist_id: Annotated[str | None, Form()] = None,
    kb_doc_ids: Annotated[str | None, Form()] = None,
) -> CallRegisterResponse:
    """Server-side upload proxy: browser → API → R2, then register calls (FR3).

    Preferred over the browser→R2 presigned PUT when the recordings bucket has no CORS
    rule (a direct PUT would be blocked by the browser and the object would never land,
    causing the transcription provider to fail with a download error). Bytes stream through
    the API to R2 with the object-scoped token, so no bucket CORS/admin access is needed.
    """
    await _require_agent(session, pid, aid)
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no files")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"at most {MAX_UPLOAD_FILES} files per upload",
        )
    # Per-portfolio in-flight cap — reject (429) before writing any bytes so nothing is uploaded
    # when over quota (no orphan R2 objects).
    await assert_inflight_quota(session, pid, len(files))

    storage = build_storage(settings)
    registered: list[tuple[str, int | None]] = []
    for f in files:
        data = await f.read()
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"empty file: {f.filename}",
            )
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"file too large: {f.filename}",
            )
        filename = PurePosixPath(f.filename or "recording").name
        key = recording_key(pid, aid, filename)
        await storage.put_recording(key, data, _audio_content_type(filename, f.content_type))
        registered.append((key, None))

    return await register_keys(
        session, pid=pid, aid=aid, keys=registered, uploaded_by=ctx.user.id,
        option=_parse_option(option),
        checklist_id=_parse_uuid(checklist_id),
        kb_doc_ids=_parse_uuid_list(kb_doc_ids),
    )
