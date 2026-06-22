"""Run the judge over the gold set and score agreement (§16.2)."""

from __future__ import annotations

from dataclasses import dataclass

from app.eval.gold import GoldExample
from app.judge.client import JudgeClient


@dataclass
class EvalResult:
    total: int          # items compared
    agreements: int     # judge answer == confirmed answer
    examples: int

    @property
    def accuracy(self) -> float:
        return self.agreements / self.total if self.total else 1.0


async def evaluate_judge(gold: list[GoldExample], judge: JudgeClient) -> EvalResult:
    total = 0
    agreements = 0
    for example in gold:
        output = await judge.evaluate(
            transcript=example.transcript_obj(), items=example.judge_items()
        )
        by_id = {str(v.checklist_item_id): v.answer for v in output.verdicts}
        for item_id, expected in example.expected.items():
            total += 1
            if by_id.get(item_id) == expected:
                agreements += 1
    return EvalResult(total=total, agreements=agreements, examples=len(gold))
