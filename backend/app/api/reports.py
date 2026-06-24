"""Report read + per-item user notes (FR11, §10, §12). The narrative is generated eagerly
by the 3-agent pipeline at judge time, so reading a report is a pure DB load."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.transcripts import _transcript_to_text
from app.audit import record_audit
from app.authz import AuthContext, authorize_report, authorize_report_item
from app.config import settings
from app.db import get_session
from app.models import Call
from app.rbac import Action
from app.reports.service import (
    load_report,
    render_and_store_report,
    render_html,
    set_agent_name,
    set_user_note,
)
from app.schemas import AgentNameUpdate, DownloadUrlOut, NoteUpdate, ReportOut
from app.storage import StorageService, build_storage

router = APIRouter(tags=["reports"])

# The individual reports that can be previewed/downloaded on their own (raw transcript is .txt).
_SECTIONS = {"feedback", "checklist", "ideal"}

# Module-level so tests can monkeypatch. R2 in prod; shared local-disk in dev.
_storage: StorageService = build_storage(settings)


@router.get("/reports/{report_id}", response_model=ReportOut)
async def get_report(
    report_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReportOut:
    report = await load_report(session, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    # Export the rendered HTML artifact to the R2 reports bucket (best-effort, idempotent).
    await render_and_store_report(session, report, storage=_storage)
    return report


@router.get("/reports/{report_id}/report:download", response_model=DownloadUrlOut)
async def download_report(
    report_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DownloadUrlOut:
    """Presigned GET for the rendered HTML report in R2 (rendered + uploaded on demand)."""
    report = await load_report(session, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    await render_and_store_report(session, report, storage=_storage)
    url = _storage.presign_report_get(report.call_id)
    return DownloadUrlOut(url=url, expires_in=settings.r2_presign_ttl_seconds)


@router.get("/reports/{report_id}/report.pdf:download", response_model=DownloadUrlOut)
async def download_report_pdf(
    report_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DownloadUrlOut:
    """Render the report to PDF (headless Chromium), store it in R2, return a signed URL.
    If Chromium isn't available, returns 503 — the HTML artifact remains the fallback."""
    from app.reports.pdf import PdfUnavailable, html_to_pdf

    report = await load_report(session, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")

    html = await render_html(session, report)
    try:
        pdf = await html_to_pdf(html)
    except PdfUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF rendering is unavailable on this server (Chromium not installed).",
        ) from exc
    await _storage.put_report_pdf(report.call_id, pdf)
    url = _storage.presign_report_pdf_get(report.call_id)
    return DownloadUrlOut(url=url, expires_in=settings.r2_presign_ttl_seconds)


@router.get("/reports/{report_id}/section.pdf")
async def download_section_pdf(
    report_id: uuid.UUID,
    section: str,
    _ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Render ONE individual report (feedback | checklist | ideal) to PDF, return the bytes."""
    from app.reports.pdf import PdfUnavailable, html_to_pdf

    if section not in _SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unknown section"
        )
    report = await load_report(session, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    html = await render_html(session, report, section=section)
    try:
        pdf = await html_to_pdf(html)
    except PdfUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF rendering is unavailable on this server (Chromium not installed).",
        ) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{section}_{str(report.call_id)[:8]}.pdf"'
        },
    )


@router.get("/reports/{report_id}/section.html")
async def download_section_html(
    report_id: uuid.UUID,
    section: str,
    _ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """ONE individual report (feedback | checklist | ideal) as standalone HTML.

    HTML (not PDF) is the download format for the report sections: the ideal conversation can run
    long and gets clipped by fixed PDF pages, whereas HTML reflows and never cuts off."""
    if section not in _SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unknown section"
        )
    report = await load_report(session, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    html = await render_html(session, report, section=section)
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{section}_{str(report.call_id)[:8]}.html"'
        },
    )


@router.get("/reports/{report_id}/transcript.txt")
async def download_report_transcript(
    report_id: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_VIEW))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """The raw transcript for this report's call, as a .txt download (no in-app preview)."""
    report = await load_report(session, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    call = await session.get(Call, report.call_id)
    if call is None or call.r2_transcript_uri is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no transcript")
    transcript = await _storage.get_transcript(call.id)
    return Response(
        content=_transcript_to_text(transcript),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="transcript_{str(call.id)[:8]}.txt"'
        },
    )


@router.post("/reports/{report_id}/save", status_code=status.HTTP_204_NO_CONTENT)
async def save_report(
    report_id: uuid.UUID,
    ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_NOTE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Finalize a verifier's review (notes are persisted per-item; this records the save in
    the activity log). REPORT_NOTE-gated, so read-only agents can't."""
    await record_audit(
        session, actor_id=ctx.user.id, action="report.save",
        entity="report", entity_id=report_id,
    )
    await session.commit()


@router.patch("/report-items/{item_id}/note", status_code=status.HTTP_204_NO_CONTENT)
async def update_note(
    item_id: uuid.UUID,
    body: NoteUpdate,
    _ctx: Annotated[AuthContext, Depends(authorize_report_item(Action.REPORT_NOTE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    if not await set_user_note(session, item_id, body.note):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="item not found")


@router.patch("/reports/{report_id}/agent-name", status_code=status.HTTP_204_NO_CONTENT)
async def update_agent_name(
    report_id: uuid.UUID,
    body: AgentNameUpdate,
    ctx: Annotated[AuthContext, Depends(authorize_report(Action.REPORT_NOTE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Override the agent name an auditor knows handled this call (replaces the auto-extracted
    one in the report, its downloads, and the CSV). REPORT_NOTE-gated, like per-item notes."""
    if not await set_agent_name(session, report_id, body.agent_name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    await record_audit(
        session, actor_id=ctx.user.id, action="report.agent_name",
        entity="report", entity_id=report_id, meta={"agent_name": body.agent_name.strip()},
    )
    await session.commit()
