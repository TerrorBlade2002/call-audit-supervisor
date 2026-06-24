"""Phase 5 DoD: report production + routing flags + objection clustering. DB-backed."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.checklists.service import create_default_checklist, get_default_checklist, get_items
from app.db import session_scope
from app.judge.client import StubJudge
from app.judge.clustering import cluster_objections
from app.judge.embeddings import StubEmbedder
from app.judge.merged import StubMerged
from app.judge.narrative import StubNarrative
from app.judge.options import ProcessingOption
from app.judge.routing import RoutingConfig
from app.judge.service import judge_call
from app.models import Agent, Call, Objection, Portfolio, ReportItem
from app.stt import Transcript, Utterance

pytestmark = pytest.mark.usefixtures("db_ready")

_CFG = RoutingConfig(confidence_threshold=0.75, min_evidence_chars=24, max_escalation_fraction=0.6)
_TRANSCRIPT = Transcript(
    transcript_id="t-1",
    duration_sec=300.0,
    text="Agent and consumer talk.",
    utterances=[Utterance(speaker="A", start_sec=0.0, end_sec=3.0, text="Hello, recorded line.")],
)


async def _seed_call() -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope() as s:
        p = Portfolio(name="P")
        s.add(p)
        await s.flush()
        await create_default_checklist(s, p.id)
        a = Agent(portfolio_id=p.id, name="A")
        s.add(a)
        await s.flush()
        c = Call(agent_id=a.id, portfolio_id=p.id, r2_audio_uri="r2://x")
        s.add(c)
        await s.flush()
    return p.id, c.id


async def test_judge_produces_report_with_routing_flags() -> None:
    pid, call_id = await _seed_call()
    async with session_scope() as s:
        await judge_call(
            s, call_id=call_id, portfolio_id=pid, transcript=_TRANSCRIPT,
            option=ProcessingOption.FULL, merged_gen=StubMerged(),
            rewriter_gen=StubNarrative(), embedder=StubEmbedder(), routing_config=_CFG,
        )

    async with session_scope() as s:
        items = list(await s.scalars(select(ReportItem)))
        objections = list(await s.scalars(select(Objection)))

    assert len(items) > 0
    assert all(i.answer in {"PASS", "FAIL", "NA"} for i in items)  # PASS/FAIL/NA only, no scores
    # Default checklist has subjective items → those are now FREE TEXT: a short written answer
    # (answer "NA", text in raw_answer), accepted as-is rather than flagged for review.
    free = [i for i in items if i.answer == "NA" and i.raw_answer]
    assert free, "expected free-text (subjective) items to carry a written answer"
    assert all(i.decided_by == "primary" for i in items)  # single tier; free-text accepted as-is
    # Objection persisted with an embedding (for clustering).
    assert len(objections) == 1
    assert objections[0].embedding is not None


async def test_second_tier_resolves_escalations() -> None:
    pid, call_id = await _seed_call()
    async with session_scope() as s:
        cl = await get_default_checklist(s, pid)
        flagged = {it.id for it in await get_items(s, cl.id)}  # flag every item to escalate
        await judge_call(
            s, call_id=call_id, portfolio_id=pid, transcript=_TRANSCRIPT,
            option=ProcessingOption.CHECKLIST_ONLY,
            judge=StubJudge(flag_item_ids=flagged), embedder=StubEmbedder(),
            routing_config=_CFG, escalation_judge=StubJudge(),
        )

    async with session_scope() as s:
        items = list(await s.scalars(select(ReportItem)))
    # With a second tier configured, escalations route to tier1 — not human review.
    assert any(i.decided_by == "tier1" for i in items)
    assert all(not i.needs_human_review for i in items)


async def test_objections_cluster_across_calls() -> None:
    pid, call_a = await _seed_call()
    # second call in the SAME portfolio
    async with session_scope() as s:
        a = Agent(portfolio_id=pid, name="A2")
        s.add(a)
        await s.flush()
        c = Call(agent_id=a.id, portfolio_id=pid, r2_audio_uri="r2://y")
        s.add(c)
        await s.flush()
        call_b = c.id

    for cid in (call_a, call_b):
        async with session_scope() as s:
            await judge_call(
                s, call_id=cid, portfolio_id=pid, transcript=_TRANSCRIPT,
                option=ProcessingOption.FULL, merged_gen=StubMerged(),
                rewriter_gen=StubNarrative(), embedder=StubEmbedder(), routing_config=_CFG,
            )

    async with session_scope() as s:
        clusters = await cluster_objections(s, pid)
    # Identical objection text across both calls → one cluster, count 2, never cleared.
    assert len(clusters) == 1
    assert clusters[0].count == 2
    assert clusters[0].never_cleared is True
