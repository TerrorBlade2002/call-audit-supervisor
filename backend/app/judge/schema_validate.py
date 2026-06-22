"""Validation for super-admin custom output schemas (Structured Outputs §B2 / phase 2).

A custom schema becomes Gemini's response schema for a judge stage, so the model output is
deterministic and shaped by the admin. But the schema is NOT a free variable — the system extracts
an operational CORE from it (verdicts → routing/DB, objections → clustering, feedback → report +
ideal). So an upload must (a) stay within the JSON-schema subset the model accepts and (b) contain
that stage's required core fields. Otherwise it's rejected with the offending path, so a schema can
never silently break the pipeline. Admins may add any extra fields on top (surfaced to the report
template under ``extra.*``).
"""

from __future__ import annotations

from typing import Any

STAGES = ("feedback", "checklist", "ideal", "merged")

# Composition keywords the model's structured-output mode does not support — reject on upload.
_UNSUPPORTED = ("$ref", "oneOf", "anyOf", "allOf", "not", "patternProperties", "$defs")
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


class SchemaError(ValueError):
    """Raised when a custom output schema is malformed or missing a required core field."""


# ── Clean default schemas per stage (no $ref) — the "Load default" starting point + the contract
#    made concrete. These mirror the built-in Pydantic models. ──────────────────────────────────
def _string() -> dict[str, Any]:
    return {"type": "string"}


_FEEDBACK_PROPS: dict[str, Any] = {
    "agent_name": {"type": ["string", "null"]},
    "summary": _string(),
    "coaching": _string(),
    "compliance": _string(),
    "feedback": {
        "type": "object",
        "properties": {
            "strengths": {"type": "array", "items": _string()},
            "development": {"type": "array", "items": _string()},
        },
        "required": ["strengths", "development"],
    },
    "objections": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "text": _string(),
                "category": _string(),
                "cleared": {"type": "boolean"},
            },
            "required": ["text"],
        },
    },
}
_FEEDBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": _FEEDBACK_PROPS,
    "required": ["summary", "coaching", "compliance", "feedback", "objections"],
}
_VERDICTS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "checklist_item_id": _string(),
            "answer": {"type": "string", "enum": ["PASS", "FAIL", "NA"]},
            "raw_answer": _string(),
            "confidence": {"type": "number"},
            "evidence_quote": _string(),
            "evidence_offset_sec": {"type": ["number", "null"]},
            "comment": _string(),
            "needs_review": {"type": "boolean"},
        },
        "required": [
            "checklist_item_id", "answer", "confidence", "evidence_quote", "needs_review",
        ],
    },
}
_CHECKLIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"verdicts": _VERDICTS_SCHEMA},
    "required": ["verdicts"],
}
_IDEAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "already_ideal": {"type": "boolean"},
        "ideal_conversation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["Agent", "Consumer"]},
                    "text": _string(),
                },
                "required": ["speaker", "text"],
            },
        },
    },
    "required": ["already_ideal", "ideal_conversation"],
}
_MERGED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"feedback": _FEEDBACK_SCHEMA, "verdicts": _VERDICTS_SCHEMA},
    "required": ["feedback", "verdicts"],
}

DEFAULT_SCHEMAS: dict[str, dict[str, Any]] = {
    "feedback": _FEEDBACK_SCHEMA,
    "checklist": _CHECKLIST_SCHEMA,
    "ideal": _IDEAL_SCHEMA,
    "merged": _MERGED_SCHEMA,
}

# Required core paths per stage. "a.b" = nested object; "a[]" = descend into an array's items.
_CONTRACTS: dict[str, list[tuple[str, str]]] = {
    "feedback": [
        ("summary", "string"), ("coaching", "string"), ("compliance", "string"),
        ("feedback", "object"), ("feedback.strengths", "array"),
        ("feedback.development", "array"), ("objections", "array"),
        ("objections[].text", "string"),
    ],
    "checklist": [
        ("verdicts", "array"), ("verdicts[].checklist_item_id", "string"),
        ("verdicts[].answer", "string"), ("verdicts[].confidence", "number"),
        ("verdicts[].evidence_quote", "string"), ("verdicts[].needs_review", "boolean"),
    ],
    "ideal": [
        ("already_ideal", "boolean"), ("ideal_conversation", "array"),
        ("ideal_conversation[].speaker", "string"), ("ideal_conversation[].text", "string"),
    ],
    "merged": [
        ("feedback", "object"), ("feedback.summary", "string"),
        ("feedback.coaching", "string"), ("feedback.compliance", "string"),
        ("feedback.feedback", "object"), ("feedback.objections", "array"),
        ("feedback.objections[].text", "string"), ("verdicts", "array"),
        ("verdicts[].checklist_item_id", "string"), ("verdicts[].answer", "string"),
        ("verdicts[].confidence", "number"), ("verdicts[].evidence_quote", "string"),
        ("verdicts[].needs_review", "boolean"),
    ],
}


def _check_subset(node: Any, where: str = "schema") -> None:
    if isinstance(node, dict):
        for bad in _UNSUPPORTED:
            if bad in node:
                raise SchemaError(f"unsupported keyword '{bad}' at {where}")
        t = node.get("type")
        types = t if isinstance(t, list) else [t] if t is not None else []
        for tv in types:
            if tv not in _TYPES:
                raise SchemaError(f"unsupported type '{tv}' at {where}")
        for k, v in node.items():
            _check_subset(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _check_subset(v, f"{where}[{i}]")


def _node_at(schema: dict[str, Any], path: str) -> dict[str, Any] | None:
    node: Any = schema
    for seg in path.split("."):
        arr = seg.endswith("[]")
        key = seg[:-2] if arr else seg
        props = node.get("properties") if isinstance(node, dict) else None
        if not isinstance(props, dict) or key not in props:
            return None
        node = props[key]
        if arr:
            if not isinstance(node, dict) or node.get("type") != "array":
                return None
            node = node.get("items", {})
    return node if isinstance(node, dict) else None


def _kind_ok(node: dict[str, Any], kind: str) -> bool:
    t = node.get("type")
    types = t if isinstance(t, list) else [t]
    if kind in types:
        return True
    # tolerate integer where number is required, and enum (no explicit type) as string
    if kind == "number" and "integer" in types:
        return True
    return bool(kind == "string" and node.get("enum") is not None)


def validate_output_schema(stage: str, schema: Any) -> None:
    """Raise SchemaError if the schema is outside the supported subset or missing a core field."""
    if stage not in DEFAULT_SCHEMAS:
        raise SchemaError(f"unknown stage '{stage}' (expected one of {', '.join(STAGES)})")
    if not isinstance(schema, dict):
        raise SchemaError("schema must be a JSON object")
    if schema.get("type") != "object":
        raise SchemaError('the top-level "type" must be "object"')
    if not isinstance(schema.get("properties"), dict):
        raise SchemaError('the schema must have an object "properties" map')
    _check_subset(schema)
    for path, kind in _CONTRACTS[stage]:
        node = _node_at(schema, path)
        if node is None:
            raise SchemaError(f"schema is missing the required field: {path}")
        if not _kind_ok(node, kind):
            raise SchemaError(f"field {path} must be of type {kind}")
