"""Eval gate (§16.2): passes on a good judge, blocks a deliberate regression."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.eval import gate
from app.eval.gold import load_gold_set
from app.eval.runner import evaluate_judge
from app.judge.client import JudgeItem, StubJudge
from app.judge.schema import ItemVerdict, JudgeOutput
from app.stt import Transcript

_GOLD = Path("backend/eval/gold_set.json")


class RegressedJudge:
    """A judge that flips every verdict to FAIL — simulates a quality regression."""

    async def evaluate(
        self, *, transcript: Transcript, items: list[JudgeItem]
    ) -> JudgeOutput:
        return JudgeOutput(
            verdicts=[
                ItemVerdict(
                    checklist_item_id=it.checklist_item_id,
                    answer="FAIL",
                    confidence=0.9,
                    evidence_quote="x" * 30,
                )
                for it in items
            ],
            objections=[],
        )


def test_gold_set_fixture_loads() -> None:
    gold = load_gold_set(_GOLD)
    assert gold
    assert gold[0].expected


async def test_gate_passes_with_consistent_judge() -> None:
    gold = load_gold_set(_GOLD)
    result = await evaluate_judge(gold, StubJudge())
    assert result.accuracy == 1.0  # stub returns PASS; fixture expects PASS


async def test_gate_fails_on_regression() -> None:
    gold = load_gold_set(_GOLD)
    result = await evaluate_judge(gold, RegressedJudge())
    assert result.accuracy < 0.9  # all flipped to FAIL → below threshold


def test_gate_main_returns_zero_with_good_judge() -> None:
    # The CLI used in CI exits 0 when the committed gold set passes.
    assert gate.main() == 0


def test_unknown_item_counts_as_disagreement() -> None:
    # Defensive: a judge that omits an item should not silently pass.
    gold = load_gold_set(_GOLD)

    class EmptyJudge:
        async def evaluate(self, *, transcript: Transcript, items: list[JudgeItem]) -> JudgeOutput:
            return JudgeOutput(verdicts=[], objections=[])

    result = asyncio.run(evaluate_judge(gold, EmptyJudge()))
    assert result.accuracy == 0.0
