"""Structured judge output (§7.3). Validated against this schema — a model that returns
malformed output fails fast rather than producing a bad report.

No numeric scoring anywhere (§12): verdicts are PASS/FAIL/NA only.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Answer = Literal["PASS", "FAIL", "NA"]

# Custom output schemas (Agent Studio) may add fields beyond the operational core; allowing extras
# means a custom schema's outputs survive validation + model_dump (→ model_passes → report
# template's ``extra.*``) instead of being silently dropped.
def _strip_additional_properties(schema: dict[str, Any]) -> None:
    schema.pop("additionalProperties", None)


_ALLOW = ConfigDict(extra="allow", json_schema_extra=_strip_additional_properties)


class ItemVerdict(BaseModel):
    model_config = _ALLOW
    checklist_item_id: uuid.UUID
    answer: Answer  # normalized verdict for routing/coloring (PASS/FAIL/NA)
    raw_answer: str = ""  # verbatim option chosen (e.g. "Yes", "Strong", "Submissive")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quote: str = ""
    evidence_offset_sec: float | None = None
    comment: str = ""
    needs_review: bool = False  # model self-flag (§7.3)


class ObjectionOut(BaseModel):
    model_config = _ALLOW
    text: str
    category: str | None = None
    cleared: bool = False


class JudgeOutput(BaseModel):
    """The whole structured-output payload for one call (checklist agent)."""

    model_config = _ALLOW
    verdicts: list[ItemVerdict] = Field(default_factory=list)
    objections: list[ObjectionOut] = Field(default_factory=list)


# ── Deterministic output schemas (used as Gemini response_schema + the parse model). ──
# response_schema makes the STRUCTURE deterministic (fields/types/enums are guaranteed); the
# agents also run at temperature=0 for maximally reproducible CONTENT.


class FeedbackBlock(BaseModel):
    model_config = _ALLOW
    strengths: list[str] = Field(default_factory=list)
    development: list[str] = Field(default_factory=list)


class FeedbackOut(BaseModel):
    """Agent 1 (feedback) structured output → the report's non-checklist/non-ideal sections."""

    model_config = _ALLOW
    agent_name: str | None = None
    summary: str = ""
    coaching: str = ""
    compliance: str = ""
    feedback: FeedbackBlock = Field(default_factory=FeedbackBlock)
    objections: list[ObjectionOut] = Field(default_factory=list)


class IdealTurn(BaseModel):
    model_config = _ALLOW
    speaker: Literal["Agent", "Consumer"]
    text: str


class IdealOut(BaseModel):
    """Agent 3 (ideal rewriter) structured output → the report's Ideal Conversation section."""

    model_config = _ALLOW
    already_ideal: bool = False
    ideal_conversation: list[IdealTurn] = Field(default_factory=list)


class MergedOut(BaseModel):
    """FULL-option merged agent: feedback + checklist verdicts in one deterministic payload."""

    model_config = _ALLOW
    feedback: FeedbackOut
    verdicts: list[ItemVerdict] = Field(default_factory=list)
