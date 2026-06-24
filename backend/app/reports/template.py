"""Deterministic, logic-less report templating (schema-hardening §B2).

A super-admin uploads an HTML template written in a small Mustache subset; we render the report
by substituting the structured report data into it — no LLM in the render path, so the layout is
byte-identical every time for a given data shape. The template is validated at upload against the
report *data dictionary* (the available fields): any reference to a field that does not exist
fails the upload with the offending path, so a template and its data can never silently drift.

Supported syntax (intentionally minimal — no logic, no code execution):
    {{ field }}            HTML-escaped value (dotted paths allowed: a.b)
    {{{ field }}} / {{& }} unescaped value
    {{# section }}…{{/}}   repeat for a list, or render once if truthy (objects/bools)
    {{^ section }}…{{/}}   render once if the section is empty/false (inverted)
    {{ . }}                the current item inside a list-of-scalars section
    {{! comment }}         ignored

This module owns BOTH the renderer and the validator so they can never disagree about what a
reference means. In phase 2 the DATA_FIELDS descriptor will be derived from the active output
schemas; today it describes the fixed report payload.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from app.checklists import is_free_text
from app.schemas import ReportOut

# ── The report data dictionary: what a template may reference. ────────────────────────────────
# Node kinds: "scalar" | "bool" | ("list", "scalar") | ("list", {field: kind, ...}).
_ITEM_FIELDS: dict[str, Any] = {
    "section": "scalar",
    "text": "scalar",
    "answer": "scalar",
    "raw_answer": "scalar",
    "confidence": "scalar",
    "evidence_quote": "scalar",
    "evidence_offset_sec": "scalar",
    "comment": "scalar",
    "needs_review": "bool",
    "is_subjective": "bool",       # free-text (qualitative) item
    "answer_display": "scalar",    # raw_answer for free-text items, else the verdict
}
_OBJECTION_FIELDS: dict[str, Any] = {
    "text": "scalar",
    "category": "scalar",
    "cleared": "bool",
}
_TURN_FIELDS: dict[str, Any] = {"speaker": "scalar", "text": "scalar"}

DATA_FIELDS: dict[str, Any] = {
    "agent_name": "scalar",
    "call_id": "scalar",
    "generated": "scalar",
    "option": "scalar",
    "flagged_for_review": "bool",
    "flag_reason": "scalar",
    "compliance_verdict": "scalar",
    "quality_verdict": "scalar",
    "summary": "scalar",
    "coaching": "scalar",
    "compliance": "scalar",
    "already_ideal": "bool",
    "has_feedback": "bool",
    "has_checklist": "bool",
    "has_ideal": "bool",
    "has_objections": "bool",
    "strengths": ("list", "scalar"),
    "development": ("list", "scalar"),
    "items": ("list", _ITEM_FIELDS),
    "objections": ("list", _OBJECTION_FIELDS),
    "ideal_conversation": ("list", _TURN_FIELDS),
    # The raw model output (incl. any custom-schema fields the admin added). Dynamic — references
    # under extra.* are accepted without strict validation, since the shape is whatever the active
    # output schema defines (e.g. {{extra.feedback.summary}}, {{extra.risk_level}}).
    "extra": "dynamic",
}


class TemplateError(ValueError):
    """Raised when a template is malformed or references a field outside the data dictionary."""


# ── Tokenizer ─────────────────────────────────────────────────────────────────────────────────
# Triple-brace (raw) MUST be the first alternative so it wins over the `{{` branch on `{{{…}}}`.
_TAG = re.compile(r"\{\{\{\s*(.*?)\s*\}\}\}|\{\{([#^/&!]?)\s*(.*?)\s*\}\}", re.DOTALL)


def _tokenize(src: str) -> list[tuple[str, str]]:
    """Flat token stream: ('text', s) | ('var', name) | ('raw', name) | ('#'|'^'|'/', name)."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _TAG.finditer(src):
        if m.start() > pos:
            out.append(("text", src[pos : m.start()]))
        pos = m.end()
        if m.group(1) is not None:  # {{{ raw }}}
            out.append(("raw", m.group(1)))
            continue
        sigil, name = m.group(2), m.group(3)
        if sigil == "!":  # comment
            continue
        if sigil == "&":
            out.append(("raw", name))
        elif sigil in ("#", "^", "/"):
            out.append((sigil, name))
        else:
            out.append(("var", name))
    if pos < len(src):
        out.append(("text", src[pos:]))
    return out


# ── Validation (upload-time) ──────────────────────────────────────────────────────────────────
def _resolve(name: str, stack: list[Any]) -> Any:
    """Resolve a (possibly dotted) field name against the schema-context stack, top-down.

    The "dynamic" node (and the "dynamic" context sentinel) accepts any continuation — used for
    ``extra.*``, whose shape is defined by the active output schema rather than known statically.
    """
    head, _, rest = name.partition(".")
    for ctx in reversed(stack):
        if ctx == "dynamic":
            return "dynamic"
        if isinstance(ctx, dict) and head in ctx:
            node: Any = ctx[head]
            if node == "dynamic":
                return "dynamic"
            if not rest:
                return node
            # dotted: descend into an object node (a plain dict of fields)
            if isinstance(node, dict):
                return _resolve(rest, [node])
            return None
    return None


def validate_template(src: str, fields: dict[str, Any] | None = None) -> None:
    """Raise TemplateError if the template is unbalanced or references an unknown field."""
    root = fields if fields is not None else DATA_FIELDS
    tokens = _tokenize(src)
    stack: list[Any] = [root]
    open_sections: list[str] = []
    for kind, name in tokens:
        if kind == "text":
            continue
        if kind in ("var", "raw"):
            if name == ".":
                if not open_sections:
                    raise TemplateError("{{.}} used outside of a section")
                continue
            if _resolve(name, stack) is None:
                raise TemplateError(f"template references unknown field: {name}")
        elif kind in ("#", "^"):
            node = _resolve(name, stack)
            if node is None:
                raise TemplateError(f"template references unknown field: {name}")
            open_sections.append(name)
            # Descend into a list's item schema (object → its fields; scalar list → empty ctx);
            # a dynamic section (extra.*) pushes a dynamic context so children resolve anything.
            if node == "dynamic":
                stack.append("dynamic")
            elif isinstance(node, tuple) and node[0] == "list":
                item = node[1]
                stack.append(item if isinstance(item, dict) else {})
            else:
                stack.append({})  # bool/scalar section: children keep outer context
        elif kind == "/":
            if not open_sections or open_sections[-1] != name:
                raise TemplateError(f"unbalanced section: {{{{/{name}}}}}")
            open_sections.pop()
            stack.pop()
    if open_sections:
        raise TemplateError(f"unclosed section: {{{{#{open_sections[-1]}}}}}")


# ── Renderer (request-time) ───────────────────────────────────────────────────────────────────
def _lookup(name: str, stack: list[Any]) -> Any:
    if name == ".":
        return stack[-1]
    head, _, rest = name.partition(".")
    for ctx in reversed(stack):
        if isinstance(ctx, dict) and head in ctx:
            val = ctx[head]
            if rest:
                return _lookup(rest, [val])
            return val
    return None


def _truthy(val: Any) -> bool:
    if isinstance(val, (list, tuple)):
        return len(val) > 0
    return bool(val)


def render_template(src: str, context: dict[str, Any]) -> str:
    """Render the Mustache-subset template with ``context``. Assumes a validated template."""
    tokens = _tokenize(src)
    pos = 0

    def render_block(stack: list[Any], stop: str | None) -> str:
        nonlocal pos
        out: list[str] = []
        while pos < len(tokens):
            kind, name = tokens[pos]
            pos += 1
            if kind == "text":
                out.append(name)
            elif kind == "var":
                val = _lookup(name, stack)
                out.append("" if val is None else html.escape(str(val)))
            elif kind == "raw":
                val = _lookup(name, stack)
                out.append("" if val is None else str(val))
            elif kind == "#":
                val = _lookup(name, stack)
                start = pos
                if isinstance(val, list):
                    if not val:
                        _skip_block(name)
                    else:
                        end = None
                        for item in val:
                            pos = start
                            out.append(render_block(stack + [item], name))
                            end = pos
                        pos = end if end is not None else pos
                elif _truthy(val):
                    out.append(render_block(stack + [val if isinstance(val, dict) else {}], name))
                else:
                    _skip_block(name)
            elif kind == "^":
                val = _lookup(name, stack)
                if _truthy(val):
                    _skip_block(name)
                else:
                    out.append(render_block(stack, name))
            elif kind == "/":
                if name == stop:
                    return "".join(out)
        return "".join(out)

    def _skip_block(name: str) -> None:
        nonlocal pos
        depth = 1
        while pos < len(tokens):
            kind, n = tokens[pos]
            pos += 1
            if kind in ("#", "^") and n == name:
                depth += 1
            elif kind == "/" and n == name:
                depth -= 1
                if depth == 0:
                    return

    return render_block([context], None)


# ── Build the data context from a report ──────────────────────────────────────────────────────
def _verdict(answer: str | None) -> str:
    a = (answer or "").upper()
    return a if a in {"PASS", "FAIL", "NA"} else "NA"


def _free(it: Any) -> bool:
    return is_free_text(
        answer_type=getattr(it, "answer_type", None),
        is_subjective=getattr(it, "is_subjective", False),
    )


def _overall(items: list[Any], compliance: bool) -> str:
    # Free-text items have no PASS/FAIL, so they don't count toward the section roll-up.
    scope = [
        it
        for it in items
        if not _free(it) and ("complian" in (it.section or "").lower()) == compliance
    ]
    if not scope:
        return "NA"
    return "FAIL" if any(_verdict(it.answer) == "FAIL" for it in scope) else "PASS"


def build_context(
    report: ReportOut,
    *,
    agent_name: str,
    created_at: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten the structured report into the template data dictionary (see DATA_FIELDS).

    ``extra`` is the raw model output (model_passes) — exposed under ``extra.*`` so a custom
    output schema's fields are reachable from the template.
    """
    narrative: dict[str, Any] = dict(report.narrative or {})
    feedback = dict(narrative.get("feedback") or {})
    items = list(report.items)
    objections = list(report.objections)

    has_feedback = bool(
        narrative.get("coaching") or narrative.get("compliance")
        or feedback.get("strengths") or feedback.get("development")
    )
    return {
        "agent_name": agent_name,
        "call_id": str(report.call_id)[:8],
        "generated": (created_at or datetime.utcnow()).strftime("%B %d, %Y"),
        "option": report.option or "",
        "flagged_for_review": bool(report.flagged_for_review),
        "flag_reason": report.flag_reason or "",
        "compliance_verdict": _overall(items, compliance=True),
        "quality_verdict": _overall(items, compliance=False),
        "summary": narrative.get("summary") or "",
        "coaching": narrative.get("coaching") or "",
        "compliance": narrative.get("compliance") or "",
        "already_ideal": bool(narrative.get("already_ideal")),
        "has_feedback": has_feedback,
        "has_checklist": bool(items),
        "has_ideal": "ideal_conversation" in narrative or "already_ideal" in narrative,
        "has_objections": bool(objections),
        "strengths": list(feedback.get("strengths") or []),
        "development": list(feedback.get("development") or []),
        "items": [
            {
                "section": it.section or "",
                "text": it.text or "",
                "answer": _verdict(it.answer),
                "raw_answer": it.raw_answer or "",
                "confidence": "" if it.confidence is None else round(it.confidence, 2),
                "evidence_quote": it.evidence_quote or "",
                "evidence_offset_sec": (
                    "" if it.evidence_offset_sec is None else it.evidence_offset_sec
                ),
                "comment": it.comment or "",
                "needs_review": bool(it.needs_human_review),
                "is_subjective": _free(it),
                # The value to show: the written answer for free-text, else the verdict.
                "answer_display": (it.raw_answer or "—") if _free(it) else _verdict(it.answer),
            }
            for it in items
        ],
        "objections": [
            {"text": o.text or "", "category": o.category or "", "cleared": bool(o.cleared)}
            for o in objections
        ],
        "ideal_conversation": [
            {"speaker": str(t.get("speaker", "")), "text": str(t.get("text", ""))}
            for t in (narrative.get("ideal_conversation") or [])
            if isinstance(t, dict)
        ],
        "extra": extra or {},
    }
