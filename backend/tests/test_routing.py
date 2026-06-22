"""Routing layer (§7.3) — escalation triggers + circuit breaker. Pure, no DB."""

from __future__ import annotations

import uuid

from app.judge.routing import (
    ItemMeta,
    RoutingConfig,
    RoutingDecision,
    Verdict,
    decide_routing,
)

_CFG = RoutingConfig(confidence_threshold=0.75, min_evidence_chars=24, max_escalation_fraction=0.6)
_GOOD_EVIDENCE = "the agent clearly stated the mini-miranda disclosure at 02:27"


def _meta(risk: str = "NORMAL", subjective: bool = False) -> ItemMeta:
    return ItemMeta(item_id=uuid.uuid4(), is_subjective=subjective, risk=risk)


def _verdict(meta: ItemMeta, **kw: object) -> Verdict:
    base: dict[str, object] = dict(
        answer="PASS", confidence=0.9, evidence_quote=_GOOD_EVIDENCE, needs_review=False
    )
    base.update(kw)
    return Verdict(item_id=meta.item_id, **base)  # type: ignore[arg-type]


def _decide_for(
    meta: ItemMeta, verdict: Verdict, *, tier_count: int = 1, overrides=None
) -> RoutingDecision:
    """Route one item-of-interest plus 4 clean fillers (so it stays well under the breaker)."""
    fillers = [_meta() for _ in range(4)]
    metas = [meta, *fillers]
    verdicts = [verdict, *[_verdict(f) for f in fillers]]
    res = decide_routing(metas, verdicts, config=_CFG, overrides=overrides, tier_count=tier_count)
    return next(d for d in res.decisions if d.item_id == meta.item_id)


def test_clean_verdict_is_accepted() -> None:
    m = _meta()
    d = _decide_for(m, _verdict(m))
    assert d.needs_human_review is False
    assert d.decided_by == "primary"
    assert d.reason is None


def test_subjective_escalates() -> None:
    m = _meta(subjective=True)
    d = _decide_for(m, _verdict(m))
    assert d.needs_human_review is True
    assert "subjective" in d.reason


def test_low_confidence_escalates() -> None:
    m = _meta()
    d = _decide_for(m, _verdict(m, confidence=0.5))
    assert d.needs_human_review is True
    assert "low confidence" in d.reason


def test_critical_fail_escalates() -> None:
    m = _meta(risk="CRITICAL")
    assert "critical" in _decide_for(m, _verdict(m, answer="FAIL")).reason
    # A critical PASS with strong evidence is NOT escalated by this rule.
    m2 = _meta(risk="CRITICAL")
    assert _decide_for(m2, _verdict(m2, answer="PASS")).reason is None


def test_model_self_flag_escalates() -> None:
    m = _meta()
    assert _decide_for(m, _verdict(m, needs_review=True)).reason == "model self-flagged"


def test_thin_evidence_escalates() -> None:
    m = _meta()
    assert "thin evidence" in _decide_for(m, _verdict(m, evidence_quote="too short")).reason


def test_learned_override_escalates() -> None:
    m = _meta()
    assert _decide_for(m, _verdict(m), overrides={m.item_id}).reason == "learned override"


def test_second_tier_routes_escalations_without_human_flag() -> None:
    # The DoD seam: adding a tier (config) sends escalations to tier1, no code change.
    m = _meta(subjective=True)
    d = _decide_for(m, _verdict(m), tier_count=2)
    assert d.decided_by == "tier1"
    assert d.needs_human_review is False
    assert d.reason is not None


def test_circuit_breaker_trips_and_flags() -> None:
    # 4 items, all escalate (subjective). Fraction 1.0 > 0.6 → breaker.
    normal = [_meta(subjective=True) for _ in range(3)]
    critical = _meta(risk="CRITICAL", subjective=True)
    metas = [*normal, critical]
    verdicts = [_verdict(m) for m in metas]
    res = decide_routing(metas, verdicts, config=_CFG)

    assert res.flagged_for_review is True
    assert res.escalation_fraction == 1.0
    # Only the elevated/critical item stays escalated; normal-risk ones are de-escalated.
    by_id = {d.item_id: d for d in res.decisions}
    assert by_id[critical.item_id].needs_human_review is True
    assert all(by_id[m.item_id].needs_human_review is False for m in normal)
