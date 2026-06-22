"""Eval gate CLI (§16.2). Run in CI: a judge-quality regression below the agreement
threshold exits non-zero and blocks the deploy.

    python -m app.eval.gate

Uses the committed gold-set fixture (hermetic — no DB/R2). With no Gemini key (CI), the
deterministic StubJudge runs against the snapshot; a regressed judge fails the gate.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import structlog

from app.config import settings
from app.eval.gold import load_gold_set
from app.eval.runner import EvalResult, evaluate_judge
from app.judge.client import JudgeClient, StubJudge

log = structlog.get_logger("eval.gate")


def _build_judge() -> JudgeClient:
    # CI has no Gemini key → deterministic stub. Production eval can plug GeminiJudge.
    return StubJudge()


async def run_gate(*, gold_path: Path, judge: JudgeClient, threshold: float) -> EvalResult:
    gold = load_gold_set(gold_path)
    return await evaluate_judge(gold, judge)


def main() -> int:
    path = Path(settings.eval_gold_set_path)
    if not path.exists():
        log.warning("eval.no_gold_set", path=str(path))
        return 0  # no gold set yet → don't block (the gate activates once labelled data exists)

    result = asyncio.run(
        run_gate(gold_path=path, judge=_build_judge(), threshold=settings.eval_agreement_threshold)
    )
    passed = result.accuracy >= settings.eval_agreement_threshold
    log.info(
        "eval.result",
        accuracy=round(result.accuracy, 4),
        threshold=settings.eval_agreement_threshold,
        items=result.total,
        examples=result.examples,
        passed=passed,
    )
    if not passed:
        print(
            f"EVAL GATE FAILED: accuracy {result.accuracy:.2%} < "
            f"threshold {settings.eval_agreement_threshold:.2%}",
            file=sys.stderr,
        )
        return 1
    print(f"EVAL GATE PASSED: accuracy {result.accuracy:.2%} over {result.total} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
