"""Gold set: labelled examples for the eval gate (§16.2).

An example is self-contained — transcript + the checklist items + the verdicts a human
confirmed CORRECT. It serializes to JSON so CI can run the gate without a DB or R2.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.judge.client import JudgeItem
from app.models import Call, ChecklistItem, Report, ReportItem, Verification
from app.storage import StorageService
from app.stt import Transcript


@dataclass
class GoldItem:
    checklist_item_id: str
    section: str
    text: str
    answer_type: str
    rubric: str
    options: list[str] = field(default_factory=list)


@dataclass
class GoldExample:
    transcript: dict[str, Any]
    items: list[GoldItem]
    expected: dict[str, str]  # checklist_item_id -> confirmed answer

    def judge_items(self) -> list[JudgeItem]:
        return [
            JudgeItem(
                checklist_item_id=uuid.UUID(i.checklist_item_id),
                section=i.section,
                text=i.text,
                answer_type=i.answer_type,
                rubric=i.rubric,
                options=i.options,
            )
            for i in self.items
        ]

    def transcript_obj(self) -> Transcript:
        return Transcript.from_dict(self.transcript)


def dump_gold_set(examples: list[GoldExample], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(e) for e in examples], indent=2), encoding="utf-8"
    )


def load_gold_set(path: Path) -> list[GoldExample]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        GoldExample(
            transcript=e["transcript"],
            items=[GoldItem(**i) for i in e["items"]],
            expected=e["expected"],
        )
        for e in raw
    ]


async def export_gold_set(
    session: AsyncSession, storage: StorageService
) -> list[GoldExample]:
    """Build gold examples from reports a verifier judged CORRECT (the confirmed truth)."""
    report_ids = list(
        await session.scalars(
            select(Verification.report_id).where(Verification.judgement == "CORRECT").distinct()
        )
    )
    examples: list[GoldExample] = []
    for report_id in report_ids:
        report = await session.get(Report, report_id)
        if report is None:
            continue
        call = await session.get(Call, report.call_id)
        if call is None:
            continue
        transcript = await storage.get_transcript(call.id)
        rows = (
            await session.execute(
                select(ReportItem, ChecklistItem)
                .join(ChecklistItem, ChecklistItem.id == ReportItem.checklist_item_id)
                .where(ReportItem.report_id == report_id, ReportItem.answer.isnot(None))
            )
        ).all()
        items = [
            GoldItem(
                checklist_item_id=str(ci.id),
                section=ci.section,
                text=ci.text,
                answer_type=ci.answer_type,
                rubric=ci.rubric_slice or ci.guidance or ci.text,
                options=ci.options or [],
            )
            for _ri, ci in rows
        ]
        expected = {str(ri.checklist_item_id): ri.answer for ri, _ci in rows if ri.answer}
        examples.append(
            GoldExample(transcript=transcript.to_dict(), items=items, expected=expected)
        )
    return examples
