"""The processing OPTION chosen at upload — it fully determines the per-call agent pipeline.

The pipeline is composed dynamically per call from its option (no fixed architecture):

    FULL (A)            checklist + KB → MERGED(feedback+checklist) → ideal
                        reports: feedback + checklist + ideal + raw
    FEEDBACK_IDEAL (C)  KB, no checklist → feedback → ideal
                        reports: feedback + ideal + raw
    CHECKLIST_ONLY (D)  checklist, no KB → checklist
                        reports: checklist + raw
    RAW_ONLY (B)        no agents, no LLM
                        reports: raw transcript only

Inputs per agent (see §7.3): the MERGED and standalone feedback/checklist agents are
multimodal (audio + transcript + their grounding); the ideal rewriter is text-only
(feedback + transcript + KB, no audio).
"""

from __future__ import annotations

import enum


class ProcessingOption(enum.StrEnum):
    FULL = "FULL"  # A — all four reports
    RAW_ONLY = "RAW_ONLY"  # B — raw transcript only
    FEEDBACK_IDEAL = "FEEDBACK_IDEAL"  # C — feedback + ideal + raw
    CHECKLIST_ONLY = "CHECKLIST_ONLY"  # D — checklist + raw


def needs_judge(opt: ProcessingOption) -> bool:
    """False only for RAW_ONLY — that call completes straight after STT, no LLM, no report."""
    return opt is not ProcessingOption.RAW_ONLY


def needs_feedback(opt: ProcessingOption) -> bool:
    return opt in (ProcessingOption.FULL, ProcessingOption.FEEDBACK_IDEAL)


def needs_checklist(opt: ProcessingOption) -> bool:
    return opt in (ProcessingOption.FULL, ProcessingOption.CHECKLIST_ONLY)


def needs_ideal(opt: ProcessingOption) -> bool:
    return opt in (ProcessingOption.FULL, ProcessingOption.FEEDBACK_IDEAL)


def uses_kb(opt: ProcessingOption) -> bool:
    """KB grounding applies to FULL and FEEDBACK_IDEAL (paths with the B selection)."""
    return opt in (ProcessingOption.FULL, ProcessingOption.FEEDBACK_IDEAL)


def merge_feedback_and_checklist(opt: ProcessingOption) -> bool:
    """FULL runs feedback+checklist as ONE merged agent (single LLM call) for efficiency."""
    return opt is ProcessingOption.FULL
