"""Report assembly for the API (§12). The narrative is produced eagerly by the 3-agent
pipeline at judge time, so reading a report is a pure DB load (no on-open LLM call)."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.judge.scope import resolve_scoped
from app.models import Agent, Call, ChecklistItem, Objection, Report, ReportItem, ReportTemplate
from app.reports.render import render_report_html
from app.reports.template import TemplateError, build_context, render_template, validate_template
from app.schemas import ReportItemOut, ReportObjectionOut, ReportOut
from app.storage import StorageService

log = structlog.get_logger("reports.service")


async def load_report(session: AsyncSession, report_id: uuid.UUID) -> ReportOut | None:
    report = await session.get(Report, report_id)
    if report is None:
        return None
    # Effective agent name: the auditor's durable override (on the call) wins over the
    # auto-extracted one; clearing the override falls back to the auto value.
    call = await session.get(Call, report.call_id)
    agent_name = (call.agent_name_override if call else None) or report.agent_name

    rows = (
        await session.execute(
            select(
                ReportItem,
                ChecklistItem.section,
                ChecklistItem.text,
                ChecklistItem.options,
                ChecklistItem.answer_type,
                ChecklistItem.is_subjective,
            )
            .join(ChecklistItem, ChecklistItem.id == ReportItem.checklist_item_id)
            .where(ReportItem.report_id == report_id)
            .order_by(ChecklistItem.sort_order)
        )
    ).all()
    items = [
        ReportItemOut(
            id=ri.id,
            checklist_item_id=ri.checklist_item_id,
            section=section,
            text=text,
            answer=ri.answer,
            raw_answer=ri.raw_answer,
            options=options,
            answer_type=answer_type,
            is_subjective=bool(is_subjective),
            confidence=ri.confidence,
            evidence_quote=ri.evidence_quote,
            evidence_offset_sec=ri.evidence_offset_sec,
            comment=ri.comment,
            decided_by=ri.decided_by,
            needs_human_review=ri.needs_human_review,
            user_note=ri.user_note,
        )
        for ri, section, text, options, answer_type, is_subjective in rows
    ]
    objections = [
        ReportObjectionOut(text=o.text, category=o.category, cleared=o.cleared)
        for o in await session.scalars(
            select(Objection).where(Objection.report_id == report_id)
        )
    ]
    return ReportOut(
        id=report.id,
        call_id=report.call_id,
        checklist_id=report.checklist_id,
        option=report.option,
        agent_name=agent_name,
        flagged_for_review=report.flagged_for_review,
        flag_reason=report.flag_reason,
        narrative=report.narrative,
        items=items,
        objections=objections,
    )


async def _report_date(session: AsyncSession, call_id: uuid.UUID) -> datetime | None:
    call = await session.get(Call, call_id)
    return call.created_at if call is not None else None


async def _folder(session: AsyncSession, call_id: uuid.UUID) -> Agent | None:
    call = await session.get(Call, call_id)
    if call is None:
        return None
    return await session.get(Agent, call.agent_id)


async def _active_template(
    session: AsyncSession, folder: Agent | None
) -> ReportTemplate | None:
    """The in-use report template for this call's (portfolio, folder), most-specific-first."""
    rows = list(
        await session.scalars(select(ReportTemplate).where(ReportTemplate.in_use.is_(True)))
    )
    if not rows:
        return None
    pid = folder.portfolio_id if folder is not None else None
    aid = folder.id if folder is not None else None
    return resolve_scoped(rows, pid, aid)


async def render_html(
    session: AsyncSession, report: ReportOut, *, section: str | None = None
) -> str:
    """Render the report (or one individual ``section``) to HTML.

    The full report uses the super-admin's in-use custom template for this call's (portfolio,
    folder) when one exists — deterministic, logic-less population of the structured report
    data. Falls back to the built-in renderer (and for individual ``section`` downloads, which
    always use the built-in layout). Prefers the transcript-derived agent name, then the folder.
    """
    folder = await _folder(session, report.call_id)
    name = report.agent_name or (folder.name if folder else None) or "Unknown agent"
    created = await _report_date(session, report.call_id)

    if section is None:
        tmpl = await _active_template(session, folder)
        if tmpl is not None:
            try:
                validate_template(tmpl.content)  # defensive — upload already validated
                db_report = await session.get(Report, report.id)
                extra = db_report.model_passes if db_report is not None else None
                return render_template(
                    tmpl.content,
                    build_context(report, agent_name=name, created_at=created, extra=extra),
                )
            except TemplateError as exc:
                log.warning(
                    "report.template_render_failed", template_id=str(tmpl.id), error=str(exc)[:160]
                )
                # fall through to the built-in renderer

    return render_report_html(report, agent_name=name, created_at=created, section=section)


async def render_and_store_report(
    session: AsyncSession, report: ReportOut, *, storage: StorageService
) -> str | None:
    """Render the report to HTML and upload it to the R2 reports bucket. Best-effort:
    returns the object key, or None if storage failed (never blocks the API response)."""
    html = await render_html(session, report)
    try:
        return await storage.put_report(report.call_id, html)
    except Exception as exc:  # noqa: BLE001 — artifact export must not break report viewing
        log.warning("report.export_failed", call_id=str(report.call_id), error=str(exc)[:120])
        return None


async def set_user_note(session: AsyncSession, item_id: uuid.UUID, note: str) -> bool:
    item = await session.get(ReportItem, item_id)
    if item is None:
        return False
    item.user_note = note
    await session.commit()
    return True


async def set_agent_name(session: AsyncSession, report_id: uuid.UUID, name: str) -> bool:
    """Override the agent name with an auditor-supplied value (blank reverts to the auto value).

    Stored on the CALL (not the report) so it survives re-processing — a re-judge rebuilds the
    report row but the call persists. The override wins everywhere the name is shown: the in-app
    header, the HTML/PDF downloads, and the checklist CSV."""
    report = await session.get(Report, report_id)
    if report is None:
        return False
    call = await session.get(Call, report.call_id)
    if call is None:
        return False
    call.agent_name_override = name.strip() or None
    await session.commit()
    return True
