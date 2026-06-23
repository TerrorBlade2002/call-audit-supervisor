# ruff: noqa: E501 — embedded CSS/HTML lines are intentionally long.
"""Render a report (the structured ReportOut + call metadata) into a self-contained HTML
artifact — the downloadable "output report" stored in the R2 reports bucket (FR11/§12).

Faithful to the supplied Everest template: the same six sections, dark glass aesthetic and
chat-bubble Ideal Conversation — but **no numeric scoring** (PASS/FAIL/NA only, matching the
codebase's verdict model) and toned-down animation so it prints cleanly. Driven entirely by
the existing report JSON; it never invents content.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from app.schemas import ReportItemOut, ReportOut


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _answer(item: ReportItemOut) -> str:
    a = (item.answer or "").upper()
    return a if a in {"PASS", "FAIL", "NA"} else "NA"


def _badge(answer: str) -> str:
    cls = {
        "PASS": "badge badge-pass",
        "FAIL": "badge badge-fail",
        "NA": "badge badge-na",
    }.get(answer, "badge badge-na")
    return f'<span class="{cls}">{_esc(answer)}</span>'


def _fmt_time(sec: float | None) -> str:
    if sec is None:
        return ""
    m, s = divmod(int(sec), 60)
    return f" <span class=\"ts\">({m}:{s:02d})</span>"


def _paragraphs(text: str | None) -> str:
    if not text:
        return '<p class="muted">No findings recorded.</p>'
    blocks = [b.strip() for b in str(text).split("\n") if b.strip()]
    return "".join(f"<p>{_esc(b)}</p>" for b in blocks)


def _list(items: list[str] | None) -> str:
    rows = items or []
    if not rows:
        return '<li class="muted">None noted.</li>'
    return "".join(f"<li>{_esc(r)}</li>" for r in rows)


def _overall(items: list[ReportItemOut], compliance: bool) -> str:
    """PASS unless any in-scope item FAILs. Compliance vs. quality split by section name."""
    scope = [
        it
        for it in items
        if ("complian" in (it.section or "").lower()) == compliance
    ]
    if not scope:
        return "NA"
    return "FAIL" if any(_answer(it) == "FAIL" for it in scope) else "PASS"


def _report_card(items: list[ReportItemOut]) -> str:
    """Group items by their checklist section into PASS/FAIL/NA tables (no scores)."""
    sections: list[tuple[str, list[ReportItemOut]]] = []
    index: dict[str, list[ReportItemOut]] = {}
    for it in items:
        sec = it.section or "Other"
        if sec not in index:
            index[sec] = []
            sections.append((sec, index[sec]))
        index[sec].append(it)

    tables = []
    for section, rows in sections:
        body = []
        for it in rows:
            review = (
                ' <span class="review">needs review</span>' if it.needs_human_review else ""
            )
            evidence = ""
            if it.evidence_quote:
                evidence = f"“{_esc(it.evidence_quote)}”{_fmt_time(it.evidence_offset_sec)}"
            notes = _esc(it.comment) if it.comment else ""
            note_block = f'<div class="note">{notes}</div>' if notes else ""
            raw = _esc(it.raw_answer) if it.raw_answer else ""
            raw_block = f' <span class="raw">{raw}</span>' if raw else ""
            body.append(
                "<tr>"
                f'<td class="item">{_esc(it.text)}{review}</td>'
                f'<td class="status">{_badge(_answer(it))}{raw_block}</td>'
                f'<td class="evidence">{evidence}{note_block}</td>'
                "</tr>"
            )
        tables.append(
            '<div class="card sub">'
            f"<h3>{_esc(section)}</h3>"
            '<table class="report-card"><thead><tr>'
            '<th>Item</th><th>Status</th><th>Evidence / Notes</th>'
            f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        )
    return "".join(tables)


# Phrases only a collections agent says — used to disambiguate stray diarization labels.
_AGENT_SIGNALS = (
    "this is an attempt to collect",
    "all calls are recorded",
    "everest receivable",
    "debt collector",
    "mini-miranda",
    "may i speak",
    "on behalf of",
    "verify the date of birth",
    "satisfaction letter",
)


def _role_map(turns: list[dict[str, Any]]) -> dict[str, str]:
    """Map each distinct speaker label to a normalized role ("Agent"/"Consumer").

    Deterministic: explicit keyword labels win; ambiguous diarization labels (A/B/Speaker N)
    are disambiguated by which one's lines contain agent-only phrases — so the agent always
    lands on the agent side regardless of what the model emitted.
    """
    labels: list[str] = []
    for t in turns:
        sp = str(t.get("speaker", "")).strip()
        if sp not in labels:
            labels.append(sp)

    mapping: dict[str, str] = {}
    ambiguous: list[str] = []
    for lbl in labels:
        low = lbl.lower()
        if low.startswith("agent") or "collector" in low or "represent" in low:
            mapping[lbl] = "Agent"
        elif (
            low.startswith("consumer")
            or "customer" in low
            or "debtor" in low
            or "caller" in low
        ):
            mapping[lbl] = "Consumer"
        else:
            ambiguous.append(lbl)

    if ambiguous:
        scores = {
            lbl: sum(
                " ".join(
                    str(t.get("text", ""))
                    for t in turns
                    if str(t.get("speaker", "")).strip() == lbl
                )
                .lower()
                .count(sig)
                for sig in _AGENT_SIGNALS
            )
            for lbl in ambiguous
        }
        agent_lbl = max(scores, key=lambda k: scores[k]) if scores else None
        for lbl in ambiguous:
            mapping[lbl] = (
                "Agent" if (lbl == agent_lbl and scores[lbl] > 0) else "Consumer"
            )
    return mapping


def _conversation(turns: list[dict[str, Any]] | None, already_ideal: bool) -> str:
    if already_ideal and not turns:
        return (
            '<p class="muted">The call was already compliant — the ideal conversation '
            "matches the original, so no rewrite was generated.</p>"
        )
    if not turns:
        return '<p class="muted">No rewritten conversation available.</p>'
    roles = _role_map(turns)
    bubbles = []
    for t in turns:
        role = roles.get(str(t.get("speaker", "")).strip(), "Consumer")
        side = "agent" if role == "Agent" else "consumer"
        bubbles.append(
            f'<div class="bubble bubble-{side}">'
            f'<span class="who">{role}:</span> {_esc(t.get("text", ""))}'
            "</div>"
        )
    return f'<div class="chat">{"".join(bubbles)}</div>'


def _objections(objections: list[Any], number: int) -> str:
    if not objections:
        return ""
    rows = []
    for o in objections:
        cleared = getattr(o, "cleared", False)
        status = (
            '<span class="badge badge-pass">Cleared</span>'
            if cleared
            else '<span class="badge badge-fail">Uncleared</span>'
        )
        rows.append(
            "<tr>"
            f'<td>{_esc(getattr(o, "text", ""))}</td>'
            f'<td>{_esc(getattr(o, "category", "") or "—")}</td>'
            f"<td>{status}</td>"
            "</tr>"
        )
    return (
        f'<section class="card"><h2>{number}. Consumer Objections</h2>'
        '<table class="report-card"><thead><tr>'
        "<th>Objection</th><th>Category</th><th>Status</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
    )


def render_report_html(
    report: ReportOut,
    *,
    agent_name: str,
    created_at: datetime | None = None,
    section: str | None = None,
) -> str:
    """Produce the standalone HTML report artifact.

    ``section`` (None | "feedback" | "checklist" | "ideal") selects ONE individual report; None
    renders the merged document. Sections present depend on the OPTION (FULL has all; others a
    subset), numbered dynamically so there are never gaps.
    """
    narrative: dict[str, Any] = dict(report.narrative or {})
    feedback = dict(narrative.get("feedback") or {})
    already_ideal = bool(narrative.get("already_ideal"))
    items = list(report.items)

    has_feedback = bool(
        narrative.get("coaching") or narrative.get("compliance")
        or feedback.get("strengths") or feedback.get("development")
    )
    has_ideal = "ideal_conversation" in narrative or "already_ideal" in narrative
    has_items = bool(items)

    comp = _overall(items, compliance=True)
    qual = _overall(items, compliance=False)
    generated = (created_at or datetime.utcnow()).strftime("%B %d, %Y")
    flagged = report.flagged_for_review

    # (kind, title, inner) — kind drives the per-section filter.
    all_blocks: list[tuple[str, str, str]] = []
    if has_feedback:
        all_blocks.append((
            "feedback", "Coaching &amp; Improvement Areas",
            f'<div class="prose">{_paragraphs(narrative.get("coaching"))}</div>',
        ))
        all_blocks.append((
            "feedback", "Compliance &amp; Quality Issues",
            f'<div class="prose">{_paragraphs(narrative.get("compliance"))}</div>',
        ))
        all_blocks.append((
            "feedback", "Constructive Feedback",
            '<div class="feedback-grid">'
            f'<div><h3 class="ok">Strengths</h3><ul>{_list(feedback.get("strengths"))}</ul></div>'
            f'<div><h3 class="fail">Areas for Development</h3>'
            f'<ul>{_list(feedback.get("development"))}</ul></div></div>',
        ))
    if has_ideal:
        all_blocks.append((
            "ideal", "Ideal Rewritten Conversation",
            _conversation(narrative.get("ideal_conversation"), already_ideal),
        ))
    if has_items:
        all_blocks.append((
            "checklist", "Quality &amp; Compliance Report Card",
            f'<div class="cards">{_report_card(items)}</div>',
        ))

    blocks = [b for b in all_blocks if section is None or b[0] == section]
    sections_html = "\n".join(
        f'<section class="card"><h2>{i}. {title}</h2>{inner}</section>'
        for i, (_kind, title, inner) in enumerate(blocks, start=1)
    )
    # Objections belong to the feedback report; pills (overall verdicts) to the checklist one.
    # Number the objections section as the one after the rendered blocks (so the feedback-only
    # export reads 1–4, not 1–3 then 6).
    show_objections = section in (None, "feedback")
    objections_html = _objections(report.objections, len(blocks) + 1) if show_objections else ""
    show_pills = has_items and section in (None, "checklist")
    pills = (
        f'<div class="pill">Compliance {_badge(comp)}</div>'
        f'<div class="pill">Quality {_badge(qual)}</div>'
        if show_pills
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Call Quality Report — {_esc(agent_name)}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;700&display=swap" rel="stylesheet">
<style>
{_CSS}
</style></head>
<body>
<div class="aura"></div>
<div class="wrap">
  <header class="report-header">
    <div class="brand">EVEREST RECEIVABLE SERVICES</div>
    <h1>Debt Collection Call Quality Report</h1>
    <div class="meta">
      <span><b>Agent:</b> {_esc(agent_name)}</span>
      <span><b>Call ID:</b> {_esc(str(report.call_id)[:8])}</span>
      <span><b>Generated:</b> {_esc(generated)}</span>
    </div>
    <div class="overall">
      <div class="pill {'pill-flag' if flagged else 'pill-ok'}">{'Flagged for Review' if flagged else 'Reviewed'}</div>
      {pills}
    </div>
    {f'<div class="flag-note">⚠ {_esc(report.flag_reason)}</div>' if flagged and report.flag_reason else ''}
  </header>

  {sections_html}

  {objections_html}

  <footer class="foot">Generated by Everest Auditor · Verdicts are PASS / FAIL / NA with cited evidence · No numeric scoring.</footer>
</div>
</body></html>"""


_CSS = """
:root{
  --bg:hsl(220,20%,10%); --bg2:hsl(220,20%,14%); --card:rgba(45,55,72,.42);
  --line:rgba(255,255,255,.12); --text:hsl(220,10%,84%); --muted:hsl(220,10%,58%);
  --ok:hsl(160,75%,52%); --fail:hsl(345,80%,66%); --info:hsl(200,75%,62%); --na:hsl(220,10%,55%);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;line-height:1.6;position:relative;min-height:100vh}
.aura{position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none;
  background:
    radial-gradient(40rem 40rem at 22% 18%, hsla(160,75%,50%,.14), transparent 60%),
    radial-gradient(38rem 38rem at 80% 82%, hsla(345,80%,62%,.13), transparent 60%);}
.wrap{position:relative;z-index:1;max-width:72rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
.report-header{text-align:center;padding:1.5rem 0 2rem}
.brand{letter-spacing:.22em;font-size:.72rem;font-weight:600;color:var(--info);text-transform:uppercase}
h1{font-family:Outfit,sans-serif;font-weight:700;font-size:2.3rem;margin:.5rem 0 1rem;
  background:linear-gradient(90deg,hsl(170,80%,68%),hsl(195,90%,76%));
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.meta{display:flex;gap:1.5rem;justify-content:center;flex-wrap:wrap;color:var(--muted);font-size:.9rem}
.meta b{color:var(--text);font-weight:600}
.overall{display:flex;gap:.75rem;justify-content:center;flex-wrap:wrap;margin-top:1.25rem}
.pill{display:inline-flex;align-items:center;gap:.5rem;background:var(--card);border:1px solid var(--line);
  border-radius:999px;padding:.4rem .9rem;font-size:.85rem;font-weight:600;backdrop-filter:blur(8px)}
.pill-ok{color:var(--ok)} .pill-flag{color:var(--fail)}
.flag-note{margin:1rem auto 0;max-width:42rem;background:hsla(345,80%,62%,.12);
  border:1px solid hsla(345,80%,62%,.4);color:hsl(345,90%,80%);border-radius:.6rem;padding:.6rem 1rem;font-size:.88rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:.9rem;padding:1.5rem 1.6rem;
  margin-top:1.5rem;backdrop-filter:blur(10px);box-shadow:0 8px 32px rgba(0,0,0,.2)}
.card h2{font-family:Outfit,sans-serif;font-size:1.45rem;font-weight:700;color:var(--info);margin:0 0 1rem}
.card.sub{margin-top:1rem;padding:1.1rem 1.2rem}
.card.sub h3{font-family:Outfit,sans-serif;font-size:1.05rem;margin:0 0 .75rem;color:var(--text)}
.prose p{margin:.5rem 0;color:var(--muted)} .muted{color:var(--muted)}
.feedback-grid{display:grid;gap:2rem;grid-template-columns:1fr 1fr}
.feedback-grid h3{font-family:Outfit,sans-serif;font-size:1.1rem;margin:0 0 .6rem}
.feedback-grid ul{margin:0;padding-left:1.1rem;color:var(--muted)} .feedback-grid li{margin:.35rem 0}
h3.ok{color:var(--ok)} h3.fail{color:var(--fail)}
.cards{display:grid;gap:1rem}
table.report-card{width:100%;border-collapse:collapse;font-size:.9rem}
table.report-card th{text-align:left;color:var(--muted);font-weight:600;font-size:.72rem;
  text-transform:uppercase;letter-spacing:.05em;padding:.4rem .6rem;border-bottom:1px solid var(--line)}
table.report-card td{padding:.6rem;border-bottom:1px solid rgba(255,255,255,.06);vertical-align:top}
td.item{color:var(--text);width:38%} td.status{width:18%;white-space:nowrap} td.evidence{color:var(--muted)}
.badge{display:inline-block;border-radius:.4rem;padding:.12rem .55rem;font-size:.74rem;font-weight:700;letter-spacing:.03em}
.badge-pass{background:hsla(160,75%,50%,.18);color:var(--ok)}
.badge-fail{background:hsla(345,80%,62%,.18);color:var(--fail)}
.badge-na{background:hsla(220,10%,60%,.18);color:var(--na)}
.raw{color:var(--muted);font-size:.78rem;margin-left:.4rem}
.review{display:inline-block;background:hsla(40,90%,60%,.18);color:hsl(40,90%,70%);
  border-radius:.35rem;padding:.05rem .4rem;font-size:.68rem;font-weight:600;margin-left:.4rem}
.ts{color:var(--info)} .note{color:var(--text);font-size:.82rem;margin-top:.3rem;opacity:.9}
.chat{display:flex;flex-direction:column;gap:.6rem;max-width:46rem;margin:0 auto}
.bubble{padding:.7rem 1rem;border-radius:1.1rem;max-width:82%;font-size:.92rem}
.bubble .who{font-weight:700}
.bubble-agent{background:hsl(220,20%,24%);border-top-left-radius:.3rem;margin-right:auto}
.bubble-agent .who{color:var(--ok)}
.bubble-consumer{background:hsl(170,70%,46%);color:hsl(220,25%,10%);border-top-right-radius:.3rem;margin-left:auto}
.bubble-consumer .who{color:hsl(220,30%,12%)}
.disp-grid{display:grid;gap:1.5rem;grid-template-columns:1fr 1fr}
.label{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.value{font-size:1.05rem;font-weight:600;color:var(--text);margin-top:.2rem}
.notation{margin-top:.4rem;background:var(--bg2);border:1px solid var(--line);border-radius:.5rem;
  padding:.8rem 1rem;font-family:ui-monospace,Menlo,monospace;font-size:.82rem;white-space:pre-wrap;color:var(--text)}
.foot{text-align:center;color:var(--muted);font-size:.78rem;margin-top:2.5rem}
@media (max-width:720px){.feedback-grid,.disp-grid{grid-template-columns:1fr}h1{font-size:1.8rem}}
@media print{body{background:#fff;color:#111}.aura{display:none}.card{box-shadow:none;break-inside:avoid}}
"""
