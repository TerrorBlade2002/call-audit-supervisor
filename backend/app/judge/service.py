"""Judge a call by composing the agent pipeline from its OPTION (§7.3).

No fixed architecture — the pipeline is assembled per call from ``ProcessingOption``:

    FULL            merged(feedback+checklist) → ideal     (KB + checklist + audio)
    FEEDBACK_IDEAL  feedback → ideal                       (KB + audio, no checklist)
    CHECKLIST_ONLY  checklist                              (checklist + audio, no KB)
    RAW_ONLY        — handled before judge_call (no report)

The whole report is assembled eagerly so opening it is a pure DB read. Idempotent: the report
is keyed by call_id and rebuilt on each run. No numeric scoring (§12).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checklists.rubric import load_kb_text
from app.checklists.service import get_default_checklist, get_items
from app.judge.client import JudgeClient, JudgeItem
from app.judge.embeddings import Embedder
from app.judge.gemini import AudioRef
from app.judge.merged import MergedGenerator
from app.judge.narrative import NarrativeGenerator, VerdictSummary
from app.judge.options import ProcessingOption, needs_checklist, needs_ideal, uses_kb
from app.judge.prompt_store import load_active_prompts
from app.judge.routing import ItemMeta, RoutingConfig, Verdict, decide_routing
from app.judge.schema import ItemVerdict
from app.judge.schema_store import load_active_schemas
from app.judge.subjective import SubjectiveGenerator
from app.models import Checklist, Objection, Report, ReportItem, RouterOverride
from app.ratelimit.backoff import FatalError
from app.stt import Transcript


async def _load_overrides(session: AsyncSession, item_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not item_ids:
        return set()
    rows = await session.scalars(
        select(RouterOverride.checklist_item_id).where(
            RouterOverride.checklist_item_id.in_(item_ids)
        )
    )
    return set(rows)


async def _resolve_checklist(
    session: AsyncSession, portfolio_id: uuid.UUID, checklist_id: uuid.UUID | None
) -> Checklist:
    """The selected checklist (by id, scoped to the portfolio) or the portfolio default."""
    if checklist_id is not None:
        cl = await session.scalar(
            select(Checklist).where(
                Checklist.id == checklist_id, Checklist.portfolio_id == portfolio_id
            )
        )
        if cl is not None:
            return cl
    cl = await get_default_checklist(session, portfolio_id)
    if cl is None:
        raise FatalError(f"no active checklist for portfolio {portfolio_id}")
    return cl


async def judge_call(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    portfolio_id: uuid.UUID,
    transcript: Transcript,
    option: ProcessingOption,
    embedder: Embedder,
    routing_config: RoutingConfig,
    agent_id: uuid.UUID | None = None,
    judge: JudgeClient | None = None,
    merged_gen: MergedGenerator | None = None,
    subjective_gen: SubjectiveGenerator | None = None,
    rewriter_gen: NarrativeGenerator | None = None,
    escalation_judge: JudgeClient | None = None,
    audio: AudioRef | None = None,
    checklist_id: uuid.UUID | None = None,
    kb_doc_ids: list[uuid.UUID] | None = None,
) -> uuid.UUID:
    # Resolve the checklist + its items only when the option produces a checklist (FULL, D).
    checklist: Checklist | None = None
    items: list[Any] = []
    judge_items: list[JudgeItem] = []
    if needs_checklist(option):
        checklist = await _resolve_checklist(session, portfolio_id, checklist_id)
        items = await get_items(session, checklist.id)
        judge_items = [
            JudgeItem(
                checklist_item_id=it.id,
                section=it.section,
                text=it.text,
                answer_type=it.answer_type,
                rubric=it.guidance or it.text,
                options=it.options or [],
            )
            for it in items
        ]

    # KB is fed to the agents that use it (FULL, FEEDBACK_IDEAL), scoped to the selected docs.
    kb = await load_kb_text(session, portfolio_id, kb_doc_ids) if uses_kb(option) else None
    # Super-admin-authored prompt bodies + output schemas in use (Agent Studio), resolved at this
    # call's (portfolio, folder) scope. Missing → built-in default body / Pydantic schema.
    active_prompts = await load_active_prompts(session, portfolio_id, agent_id)
    active_schemas = await load_active_schemas(session, portfolio_id, agent_id)

    feedback: dict[str, object] = {}
    verdict_by_id: dict[uuid.UUID, ItemVerdict] = {}
    model_passes: dict[str, Any] | None = None

    if option is ProcessingOption.FULL:
        if merged_gen is None:
            raise FatalError("FULL option requires the merged feedback+checklist agent")
        merged = await merged_gen.evaluate(
            transcript=transcript, items=judge_items, audio=audio, kb=kb,
            system_prompt=active_prompts.get("merged"),
            schema_override=active_schemas.get("merged"),
        )
        feedback = merged.feedback.model_dump()
        verdict_by_id = {v.checklist_item_id: v for v in merged.verdicts}
        model_passes = merged.model_dump(mode="json")
    elif option is ProcessingOption.CHECKLIST_ONLY:
        if judge is None:
            raise FatalError("CHECKLIST_ONLY option requires the checklist agent")
        output = await judge.evaluate(
            transcript=transcript, items=judge_items, audio=audio, kb=None,
            system_prompt=active_prompts.get("checklist"),
            schema_override=active_schemas.get("checklist"),
        )
        verdict_by_id = {v.checklist_item_id: v for v in output.verdicts}
        model_passes = output.model_dump(mode="json")
    elif option is ProcessingOption.FEEDBACK_IDEAL:
        if subjective_gen is None:
            raise FatalError("FEEDBACK_IDEAL option requires the feedback agent")
        feedback = await subjective_gen.generate(
            transcript=transcript, audio=audio, kb=kb,
            system_prompt=active_prompts.get("feedback"),
            schema_override=active_schemas.get("feedback"),
        )
        model_passes = {"feedback": feedback}

    # Routing applies only when there are checklist verdicts (FULL, CHECKLIST_ONLY).
    flagged_for_review = False
    flag_reason: str | None = None
    decision_by_id: dict[uuid.UUID, Any] = {}
    if items:
        metas = [
            ItemMeta(item_id=it.id, is_subjective=it.is_subjective, risk=it.risk) for it in items
        ]
        routing_verdicts = [
            Verdict(
                item_id=v.checklist_item_id,
                answer=v.answer,
                confidence=v.confidence,
                evidence_quote=v.evidence_quote,
                needs_review=v.needs_review,
            )
            for v in verdict_by_id.values()
        ]
        overrides = await _load_overrides(session, [it.id for it in items])
        tier_count = 2 if escalation_judge is not None else 1
        routing = decide_routing(
            metas, routing_verdicts, config=routing_config, overrides=overrides,
            tier_count=tier_count,
        )
        if escalation_judge is not None and routing.escalated_item_ids:
            esc_ids = set(routing.escalated_item_ids)
            escalated = [ji for ji in judge_items if ji.checklist_item_id in esc_ids]
            higher = await escalation_judge.evaluate(
                transcript=transcript, items=escalated, audio=audio, kb=kb,
                system_prompt=active_prompts.get("checklist"),
                schema_override=active_schemas.get("checklist"),
            )
            for v in higher.verdicts:
                verdict_by_id[v.checklist_item_id] = v
        flagged_for_review = routing.flagged_for_review
        flag_reason = routing.flag_reason
        decision_by_id = {d.item_id: d for d in routing.decisions}

    # Ideal Rewritten Conversation (FULL, FEEDBACK_IDEAL) — derives from the feedback + verdicts.
    narrative: dict[str, object] = (
        {k: feedback.get(k) for k in ("summary", "coaching", "compliance", "feedback")}
        if feedback
        else {}
    )
    if needs_ideal(option) and rewriter_gen is not None:
        summaries = []
        for it in items:
            ver = verdict_by_id.get(it.id)
            summaries.append(
                VerdictSummary(
                    section=it.section,
                    text=it.text,
                    answer=ver.answer if ver else None,
                    comment=ver.comment if ver else None,
                    evidence_quote=ver.evidence_quote if ver else None,
                )
            )
        rewrite = await rewriter_gen.generate(
            transcript=transcript, verdicts=summaries, subjective=feedback, kb=kb,
            system_prompt=active_prompts.get("ideal"),
            schema_override=active_schemas.get("ideal"),
        )
        narrative["already_ideal"] = rewrite.get("already_ideal")
        narrative["ideal_conversation"] = rewrite.get("ideal_conversation")

    # --- assemble + persist (rebuild for idempotency) ---
    _agent_name = feedback.get("agent_name") if feedback else None
    await session.execute(delete(Report).where(Report.call_id == call_id))
    report = Report(
        call_id=call_id,
        checklist_id=checklist.id if checklist is not None else None,
        option=option.value,
        model_passes=model_passes,
        agent_name=str(_agent_name) if _agent_name else None,
        narrative=narrative or None,
        flagged_for_review=flagged_for_review,
        flag_reason=flag_reason,
    )
    session.add(report)
    await session.flush()

    for it in items:
        verdict = verdict_by_id.get(it.id)
        d = decision_by_id.get(it.id)
        session.add(
            ReportItem(
                report_id=report.id,
                checklist_item_id=it.id,
                answer=verdict.answer if verdict else None,
                raw_answer=verdict.raw_answer if verdict else None,
                confidence=verdict.confidence if verdict else None,
                evidence_quote=verdict.evidence_quote if verdict else None,
                evidence_offset_sec=verdict.evidence_offset_sec if verdict else None,
                comment=verdict.comment if verdict else None,
                decided_by=d.decided_by if d else None,
                needs_human_review=d.needs_human_review if d else False,
            )
        )

    # Objections are surfaced by the feedback agent (FULL, FEEDBACK_IDEAL) — embed for clustering.
    raw_objections = feedback.get("objections") if feedback else None
    objection_rows = [
        o
        for o in (raw_objections if isinstance(raw_objections, list) else [])
        if isinstance(o, dict) and str(o.get("text") or "").strip()
    ]
    if objection_rows:
        vectors = await embedder.embed([str(o["text"]) for o in objection_rows])
        for obj, vec in zip(objection_rows, vectors, strict=True):
            session.add(
                Objection(
                    report_id=report.id,
                    text=str(obj["text"]),
                    category=str(obj["category"]) if obj.get("category") else None,
                    cleared=bool(obj.get("cleared", False)),
                    embedding=vec,
                )
            )

    await session.flush()
    return report.id
