"""Routing layer (§7.3) — pure decision logic, no I/O.

Per item, decide whether the primary verdict is accepted or escalated. v1 runs a single
tier, so escalation resolves to **human-review flagging**; the layer accepts more tiers via
config with no code change (``tier_count`` > 1 routes escalations to the next tier instead).

A circuit breaker prevents a degenerate input (bad audio, mismatched checklist) from
escalating everything: above ``max_escalation_fraction`` we escalate only the elevated/
critical-risk items and flag the whole call for review.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

# risk ordering for the circuit breaker
_RISK_RANK = {"NORMAL": 0, "ELEVATED": 1, "CRITICAL": 2}
_ELEVATED = _RISK_RANK["ELEVATED"]


@dataclass(frozen=True)
class RoutingConfig:
    confidence_threshold: float
    min_evidence_chars: int
    max_escalation_fraction: float


@dataclass(frozen=True)
class ItemMeta:
    item_id: uuid.UUID
    is_subjective: bool
    risk: str  # NORMAL | ELEVATED | CRITICAL


@dataclass(frozen=True)
class Verdict:
    item_id: uuid.UUID
    answer: str          # PASS | FAIL | NA
    confidence: float
    evidence_quote: str
    needs_review: bool


@dataclass(frozen=True)
class RoutingDecision:
    item_id: uuid.UUID
    needs_human_review: bool
    decided_by: str          # "primary" | "tier1" | "human"
    reason: str | None       # why escalated (None if accepted as-is)


@dataclass(frozen=True)
class RoutingResult:
    decisions: list[RoutingDecision]
    flagged_for_review: bool
    flag_reason: str | None
    escalation_fraction: float

    @property
    def escalated_item_ids(self) -> list[uuid.UUID]:
        return [d.item_id for d in self.decisions if d.reason is not None]


def _escalation_reason(
    meta: ItemMeta,
    verdict: Verdict,
    cfg: RoutingConfig,
    overrides: set[uuid.UUID],
) -> str | None:
    """Return the reason this item should escalate, or None to accept the primary verdict."""
    if meta.item_id in overrides:
        return "learned override"
    if meta.is_subjective:
        return "subjective item"
    if verdict.confidence < cfg.confidence_threshold:
        return f"low confidence ({verdict.confidence:.2f})"
    if meta.risk == "CRITICAL" and verdict.answer == "FAIL":
        return "critical-risk FAIL"
    if verdict.needs_review:
        return "model self-flagged"
    if verdict.answer in ("PASS", "FAIL") and len(verdict.evidence_quote) < cfg.min_evidence_chars:
        return "thin evidence"
    return None


def decide_routing(
    metas: list[ItemMeta],
    verdicts: list[Verdict],
    *,
    config: RoutingConfig,
    overrides: set[uuid.UUID] | None = None,
    tier_count: int = 1,
) -> RoutingResult:
    """Resolve routing for every item (§7.3). ``tier_count`` > 1 sends escalations to the
    next tier (``decided_by="tier1"``); otherwise they become human-review flags.
    """
    overrides = overrides or set()
    by_id = {m.item_id: m for m in metas}
    verdict_by_id = {v.item_id: v for v in verdicts}

    raw: dict[uuid.UUID, str] = {}
    for meta in metas:
        v = verdict_by_id.get(meta.item_id)
        if v is None:
            continue
        reason = _escalation_reason(meta, v, config, overrides)
        if reason is not None:
            raw[meta.item_id] = reason

    total = len(metas) or 1
    fraction = len(raw) / total

    flagged = False
    flag_reason: str | None = None
    escalated = raw
    if fraction > config.max_escalation_fraction:
        # Circuit breaker: systemic problem — don't escalate everything.
        flagged = True
        flag_reason = (
            f"escalation fraction {fraction:.0%} exceeded "
            f"{config.max_escalation_fraction:.0%}; escalating elevated-risk items only"
        )
        escalated = {
            iid: r
            for iid, r in raw.items()
            if _RISK_RANK.get(by_id[iid].risk, 0) >= _ELEVATED
        }

    decisions: list[RoutingDecision] = []
    for meta in metas:
        reason = escalated.get(meta.item_id)
        if reason is None:
            decisions.append(
                RoutingDecision(meta.item_id, False, "primary", None)
            )
        elif tier_count > 1:
            # A higher tier exists → it re-judges this item (service runs it).
            decisions.append(
                RoutingDecision(meta.item_id, False, "tier1", reason)
            )
        else:
            # Single tier → human review.
            decisions.append(
                RoutingDecision(meta.item_id, True, "primary", reason)
            )

    return RoutingResult(decisions, flagged, flag_reason, fraction)
