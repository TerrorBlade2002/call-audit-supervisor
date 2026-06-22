# ruff: noqa: E501  — the HTML/CSS template lines are intentionally long.
"""Deterministic batch "smoke" summaries (§ batch triage). No LLM.

Checklist summary → JSON for the in-app triage screen + a CSV. Feedback summary → a standalone
HTML document (download only). Everything is aggregated on demand from existing rows; agents are
keyed on the transcript-extracted ``Report.agent_name`` (folder name as fallback).
"""

from __future__ import annotations

import csv
import html
import io
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Call, ChecklistItem, Job, Objection, Report, ReportItem

TOP_K = 5


def _esc(s: Any) -> str:
    return html.escape(str(s or ""))


# ─────────────────────────────────────────────────────────── checklist summary ──


async def checklist_summary(
    session: AsyncSession, pid: uuid.UUID, bid: uuid.UUID
) -> dict[str, Any]:
    """Per-agent pass/fail, top items failed by the most agents, worst calls, batch hygiene."""
    call_rows = (
        await session.execute(
            select(Call.id, Job.state)
            .join(Job, Job.call_id == Call.id, isouter=True)
            .where(Call.batch_id == bid, Call.portfolio_id == pid)
        )
    ).all()
    total_calls = len(call_rows)
    failed_processing = sum(1 for _cid, st in call_rows if st == "FAILED")

    rep_rows = (
        await session.execute(
            select(
                Report.id, Report.call_id, Report.agent_name, Report.flagged_for_review, Agent.name
            )
            .join(Call, Call.id == Report.call_id)
            .join(Agent, Agent.id == Call.agent_id, isouter=True)
            .where(Call.batch_id == bid, Call.portfolio_id == pid, Report.checklist_id.isnot(None))
        )
    ).all()
    report_ids = [r[0] for r in rep_rows]
    missing_agent_name = sum(1 for r in rep_rows if not r[2])

    by_report: dict[uuid.UUID, list[tuple[str, str, str, str | None, bool]]] = defaultdict(list)
    if report_ids:
        item_rows = (
            await session.execute(
                select(
                    ReportItem.report_id, ChecklistItem.text, ChecklistItem.section,
                    ChecklistItem.risk, ReportItem.answer, ReportItem.needs_human_review,
                )
                .join(ChecklistItem, ChecklistItem.id == ReportItem.checklist_item_id)
                .where(ReportItem.report_id.in_(report_ids))
            )
        ).all()
        for rid, text, section, risk, answer, nr in item_rows:
            by_report[rid].append((text, section, risk or "NORMAL", answer, bool(nr)))

    # Per-agent rollup + per-call worst list + per-item agent-failure index.
    agents: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "passes": 0, "fails": 0, "critical": 0, "fail_gt_pass": 0,
                 "worst": None, "worst_score": (-1, -1)}
    )
    item_fail_agents: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    item_fail_calls: dict[tuple[str, str, str], int] = defaultdict(int)
    worst_calls: list[dict[str, Any]] = []
    clean = need_review = critical_total = 0

    for rid, call_id, agent_name, flagged, folder in rep_rows:
        agent = agent_name or folder or "—"
        items = by_report.get(rid, [])
        passes = sum(1 for it in items if it[3] == "PASS")
        fails = [it for it in items if it[3] == "FAIL"]
        critical = sum(1 for it in fails if it[2].upper() == "CRITICAL")
        needs_review = any(it[4] for it in items)
        critical_total += critical
        if not fails:
            clean += 1
        if flagged or needs_review:
            need_review += 1
        a = agents[agent]
        a["calls"] += 1
        a["passes"] += passes
        a["fails"] += len(fails)
        a["critical"] += critical
        if len(fails) > passes:
            a["fail_gt_pass"] += 1
        score = (critical, len(fails))
        if score > a["worst_score"]:
            a["worst_score"] = score
            a["worst"] = rid
        for text, section, risk, _ans, _nr in fails:
            key = (text, section, risk)
            item_fail_agents[key].add(agent)
            item_fail_calls[key] += 1
        worst_calls.append({
            "call_id": call_id, "report_id": rid, "agent": agent, "fails": len(fails),
            "critical": critical, "flagged": flagged, "needs_review": needs_review,
        })

    per_agent: list[dict[str, Any]] = [
        {"agent": k, "calls": v["calls"], "passes": v["passes"], "fails": v["fails"],
         "critical_fails": v["critical"], "calls_fail_gt_pass": v["fail_gt_pass"],
         "worst_report_id": v["worst"]}
        for k, v in agents.items()
    ]
    per_agent.sort(key=lambda x: (-x["critical_fails"], -x["fails"]))
    top_failed_items: list[dict[str, Any]] = [
        {"text": k[0], "section": k[1], "risk": k[2],
         "agents_failed": len(item_fail_agents[k]), "calls_failed": item_fail_calls[k]}
        for k in item_fail_agents
    ]
    top_failed_items.sort(key=lambda x: (-x["agents_failed"], -x["calls_failed"]))
    top_failed_items = top_failed_items[:TOP_K]
    worst_calls.sort(key=lambda x: (-x["critical"], -x["fails"]))

    return {
        "total_calls": total_calls,
        "agents": len(agents),
        "clean": clean,
        "need_review": need_review,
        "critical_fails": critical_total,
        "failed_processing": failed_processing,
        "missing_agent_name": missing_agent_name,
        "per_agent": per_agent,
        "top_failed_items": top_failed_items,
        "worst_calls": worst_calls[:8],
    }


def checklist_summary_csv(summary: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["PER AGENT"])
    w.writerow(["Agent", "Calls", "Pass", "Fail", "Critical fails", "Calls fail>pass"])
    for a in summary["per_agent"]:
        w.writerow([a["agent"], a["calls"], a["passes"], a["fails"], a["critical_fails"],
                    a["calls_fail_gt_pass"]])
    w.writerow([])
    w.writerow(["TOP FAILED ITEMS (by # agents)"])
    w.writerow(["Item", "Section", "Risk", "Agents failed", "Calls failed"])
    for it in summary["top_failed_items"]:
        w.writerow([it["text"], it["section"], it["risk"], it["agents_failed"], it["calls_failed"]])
    return buf.getvalue()


# ──────────────────────────────────────────────────────────── feedback summary ──


async def feedback_summary_html(
    session: AsyncSession, pid: uuid.UUID, bid: uuid.UUID
) -> str:
    """Agent-wise coaching briefs + objection rollups + a directed action list, as standalone HTML."""
    rep_rows = (
        await session.execute(
            select(Report.id, Report.call_id, Report.agent_name, Report.narrative, Agent.name)
            .join(Call, Call.id == Report.call_id)
            .join(Agent, Agent.id == Call.agent_id, isouter=True)
            .where(
                Call.batch_id == bid, Call.portfolio_id == pid,
                Report.option.in_(("FULL", "FEEDBACK_IDEAL")),
            )
        )
    ).all()
    report_ids = [r[0] for r in rep_rows]

    obj_rows: list[Any] = []
    if report_ids:
        obj_rows = list(
            (
                await session.execute(
                    select(Objection.report_id, Objection.text, Objection.category, Objection.cleared)
                    .where(Objection.report_id.in_(report_ids))
                )
            ).all()
        )
    agent_of_report = {r[0]: (r[2] or r[4] or "—") for r in rep_rows}

    # Per-agent aggregation.
    agents: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "summaries": [], "development": [], "compliance_concern": 0,
                 "faced": 0, "unresolved": 0, "unresolved_cat": defaultdict(int)}
    )
    for _rid, _call, agent_name, narrative, folder in rep_rows:
        agent = agent_name or folder or "—"
        n = narrative or {}
        a = agents[agent]
        a["calls"] += 1
        if n.get("summary"):
            a["summaries"].append(str(n["summary"]))
        fb = n.get("feedback") or {}
        a["development"].extend([str(d) for d in (fb.get("development") or [])])
        if str(n.get("compliance") or "").strip():
            a["compliance_concern"] += 1

    # Objection rollups (batch-wide + per agent), grouped by category (or text fallback).
    faced_by_cat: dict[str, int] = defaultdict(int)
    failed_by_cat: dict[str, int] = defaultdict(int)
    failed_cat_agents: dict[str, set[str]] = defaultdict(set)
    rep_text: dict[str, str] = {}
    for rid, text, category, cleared in obj_rows:
        agent = agent_of_report.get(rid, "—")
        cat = (category or text or "objection").strip()
        rep_text.setdefault(cat, text or cat)
        faced_by_cat[cat] += 1
        agents[agent]["faced"] += 1
        if not cleared:
            failed_by_cat[cat] += 1
            failed_cat_agents[cat].add(agent)
            agents[agent]["unresolved"] += 1
            agents[agent]["unresolved_cat"][cat] += 1

    top_faced = sorted(faced_by_cat.items(), key=lambda x: -x[1])[:TOP_K]
    top_failed = sorted(failed_by_cat.items(), key=lambda x: -x[1])[:TOP_K]
    agents_by_risk = sorted(
        ((k, v) for k, v in agents.items() if v["faced"]),
        key=lambda x: -x[1]["unresolved"],
    )[:TOP_K]

    # Directed actions (deterministic templates).
    actions: list[str] = []
    for agent, v in agents_by_risk:
        if v["unresolved"]:
            top_cat = max(v["unresolved_cat"].items(), key=lambda x: x[1])[0]
            actions.append(
                f"Coach {agent} on “{rep_text.get(top_cat, top_cat)}” "
                f"({v['unresolved']}/{v['faced']} unresolved)."
            )
    for agent, v in agents.items():
        if v["compliance_concern"]:
            actions.append(f"Review {agent} for compliance ({v['compliance_concern']} concern calls).")
    actions = actions[:TOP_K]

    # ---- render HTML ----
    def agent_block(agent: str, v: dict[str, Any]) -> str:
        main = "—"
        if v["unresolved_cat"]:
            top_cat = max(v["unresolved_cat"].items(), key=lambda x: x[1])[0]
            main = f"unresolved “{_esc(rep_text.get(top_cat, top_cat))}” objections"
        elif v["development"]:
            main = _esc(v["development"][0])
        sums = "".join(f"<li>{_esc(s)}</li>" for s in v["summaries"][:8])
        return (
            f'<div class="agent"><h3>{_esc(agent)} · {v["calls"]} call(s)</h3>'
            f'<p><b>Main issue:</b> {main}</p>'
            f'<p><b>Watch:</b> {v["unresolved"]} uncleared objection(s), '
            f'{v["compliance_concern"]} compliance-concern call(s)</p>'
            f'<p class="muted">Per-call summaries:</p><ul>{sums or "<li>—</li>"}</ul></div>'
        )

    agent_blocks = "".join(
        agent_block(k, v)
        for k, v in sorted(agents.items(), key=lambda x: -x[1]["unresolved"])
    )
    faced_rows = "".join(
        f"<li>{_esc(rep_text.get(c, c))} · faced {n}×"
        f"{(' · ' + str(failed_by_cat[c]) + ' unresolved') if failed_by_cat.get(c) else ''}</li>"
        for c, n in top_faced
    )
    failed_rows = "".join(
        f"<li>{_esc(rep_text.get(c, c))} · {n} unresolved · "
        f"{', '.join(sorted(failed_cat_agents[c]))}</li>"
        for c, n in top_failed
    )
    risk_rows = "".join(
        f"<li>{_esc(k)} · {v['unresolved']} unresolved / {v['faced']} faced</li>"
        for k, v in agents_by_risk
    )
    action_rows = "".join(f"<li>{_esc(a)}</li>" for a in actions) or "<li>No actions flagged.</li>"

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Batch Feedback Summary</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;margin:2rem auto;
padding:0 1.2rem;color:#1f2430;line-height:1.5}}
h1{{font-size:1.5rem}}h2{{font-size:1.05rem;margin-top:1.6rem;border-bottom:1px solid #eee;padding-bottom:.3rem}}
h3{{font-size:.98rem;margin:.2rem 0}}.agent{{border:1px solid #eee;border-radius:10px;padding:.6rem .9rem;margin:.6rem 0;background:#fafafa}}
.muted{{color:#888;font-size:.85rem;margin:.3rem 0 .1rem}}ul{{margin:.2rem 0 .4rem;padding-left:1.2rem}}
li{{margin:.15rem 0}}.actions{{background:#fff7f0;border:1px solid #f3d9c4;border-radius:10px;padding:.6rem .9rem}}
</style></head><body>
<h1>Batch Feedback Summary</h1>
<div class="actions"><b>Directed actions</b><ul>{action_rows}</ul></div>
<h2>Agent coaching briefs</h2>{agent_blocks or "<p>No feedback reports in this batch.</p>"}
<h2>Top objections faced</h2><ul>{faced_rows or "<li>—</li>"}</ul>
<h2>Top objections failed (unresolved)</h2><ul>{failed_rows or "<li>—</li>"}</ul>
<h2>Agents by objection risk</h2><ul>{risk_rows or "<li>—</li>"}</ul>
</body></html>"""
