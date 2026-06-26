"""Knowledge-base upload + registration (FR5). KB docs are retained (no 30-day lifecycle).

Same browser→R2 presigned pattern as recordings, but into the KB bucket. Registered
documents drive rubric distillation (§7.2): changing the KB changes the content hash and
triggers re-distillation.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import PurePosixPath
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.authz import AuthContext, authorize
from app.config import settings
from app.db import get_session
from app.kb.extract import extract_pdf_text, sha256_hex
from app.models import Document
from app.rbac import Action
from app.schemas import (
    DocumentOut,
    DocumentRegisterRequest,
    KbPresignRequest,
    PresignItem,
    PresignResponse,
    RenameRequest,
)
from app.storage import build_s3_client, kb_key, presign_put

router = APIRouter(tags=["kb"])
log = structlog.get_logger("kb")


def _download_and_extract(key: str) -> tuple[str, int] | None:
    """Sync R2 download + PDF text extraction. Runs in a thread (boto3 is blocking)."""
    try:
        obj = build_s3_client(settings, fast_fail=True).get_object(
            Bucket=settings.r2_bucket_kb, Key=key
        )
        return extract_pdf_text(obj["Body"].read())
    except Exception as exc:  # noqa: BLE001 — extraction is best-effort
        log.warning("kb.extract_failed", key=key, error=str(exc))
        return None


@router.post("/portfolios/{pid}/kb:presign", response_model=PresignResponse)
async def presign_kb(
    pid: uuid.UUID,
    body: KbPresignRequest,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.KB_MANAGE))],
) -> PresignResponse:
    client = build_s3_client(settings)
    bucket = settings.r2_bucket_kb
    ttl = settings.r2_presign_ttl_seconds
    uploads = [
        PresignItem(
            filename=f.filename,
            key=(key := kb_key(pid, f.filename)),
            upload_url=presign_put(client, bucket, key, ttl=ttl),
        )
        for f in body.files
    ]
    return PresignResponse(bucket=bucket, expires_in=ttl, uploads=uploads)


@router.post("/portfolios/{pid}/kb", response_model=list[DocumentOut], status_code=201)
async def register_kb(
    pid: uuid.UUID,
    body: DocumentRegisterRequest,
    ctx: Annotated[AuthContext, Depends(authorize(Action.KB_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Document]:
    prefix = f"{pid}/"
    docs: list[Document] = []
    for item in body.items:
        if not item.key.startswith(prefix):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="key outside portfolio scope"
            )
        doc = Document(
            portfolio_id=pid, r2_uri=item.key, page_count=item.page_count, sha256=item.sha256
        )
        # Extract plaintext to ground rubric distillation (best-effort, off the event loop).
        extracted = await asyncio.to_thread(_download_and_extract, item.key)
        if extracted is not None:
            doc.text, pages = extracted
            doc.page_count = doc.page_count or pages
        session.add(doc)
        docs.append(doc)
    await session.flush()
    await record_audit(
        session, actor_id=ctx.user.id, action="kb.register",
        entity="portfolio", entity_id=pid, meta={"count": len(docs)},
    )
    await session.commit()
    return docs


@router.post("/portfolios/{pid}/kb/upload", response_model=list[DocumentOut], status_code=201)
async def upload_kb(
    pid: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.KB_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    files: Annotated[list[UploadFile], File()],
) -> list[Document]:
    """Server-side KB upload: browser → API → R2 (no bucket CORS), extract text, register.

    Changing the KB changes its content hash → the next judge re-distills the rubric (§7.2).
    """
    s3 = build_s3_client(settings)
    docs: list[Document] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        filename = PurePosixPath(f.filename or "document.pdf").name
        key = kb_key(pid, filename)
        await asyncio.to_thread(
            s3.put_object,
            Bucket=settings.r2_bucket_kb,
            Key=key,
            Body=data,
            ContentType=f.content_type or "application/pdf",
        )
        text, pages = None, None
        try:
            text, pages = await asyncio.to_thread(extract_pdf_text, data)
        except Exception as exc:  # noqa: BLE001 — extraction is best-effort
            log.warning("kb.extract_failed", filename=filename, error=str(exc)[:80])
        doc = Document(
            portfolio_id=pid, r2_uri=key, filename=filename,
            page_count=pages, sha256=sha256_hex(data), text=text,
        )
        session.add(doc)
        docs.append(doc)
    await session.flush()
    await record_audit(
        session, actor_id=ctx.user.id, action="kb.upload",
        entity="portfolio", entity_id=pid, meta={"count": len(docs)},
    )
    await session.commit()
    return docs


@router.patch("/portfolios/{pid}/kb/{doc_id}/rename", response_model=DocumentOut)
async def rename_kb(
    pid: uuid.UUID,
    doc_id: uuid.UUID,
    body: RenameRequest,
    ctx: Annotated[AuthContext, Depends(authorize(Action.KB_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Document:
    """Rename a KB document's display name (local to this portfolio's copy — independent of any
    copies shared to other portfolios; the underlying R2 object is unchanged)."""
    doc = await session.scalar(
        select(Document).where(Document.id == doc_id, Document.portfolio_id == pid)
    )
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    doc.filename = body.name.strip()
    await record_audit(
        session, actor_id=ctx.user.id, action="kb.rename",
        entity="document", entity_id=doc_id, meta={"filename": body.name.strip()},
    )
    await session.commit()
    await session.refresh(doc)
    return doc


@router.delete("/portfolios/{pid}/kb/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(
    pid: uuid.UUID,
    doc_id: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize(Action.KB_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    doc = await session.scalar(
        select(Document).where(Document.id == doc_id, Document.portfolio_id == pid)
    )
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    try:
        await asyncio.to_thread(
            build_s3_client(settings).delete_object,
            Bucket=settings.r2_bucket_kb,
            Key=doc.r2_uri,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort R2 delete
        log.warning("kb.delete_object_failed", key=doc.r2_uri, error=str(exc)[:80])
    await session.delete(doc)
    await record_audit(
        session, actor_id=ctx.user.id, action="kb.delete",
        entity="document", entity_id=doc_id, meta={"filename": doc.filename},
    )
    await session.commit()


@router.get("/portfolios/{pid}/kb", response_model=list[DocumentOut])
async def list_kb(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.KB_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Document]:
    rows = await session.scalars(
        select(Document).where(Document.portfolio_id == pid).order_by(Document.created_at)
    )
    return list(rows)
