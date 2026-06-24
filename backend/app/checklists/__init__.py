"""Checklists: default seed data + builder/versioning service (Phase 4)."""

from __future__ import annotations


def is_free_text(*, answer_type: str | None, is_subjective: bool) -> bool:
    """Whether a checklist item is FREE TEXT (qualitative) rather than a PASS/FAIL/NA verdict.

    Free-text items (the "subjective" ones — e.g. "What objections did the agent face?") can't be
    answered yes/no, so the judge writes a short, precise sentence instead of a verdict. The
    "subjective" toggle in the builder sets both flags; ``answer_type == "TEXT"`` covers items
    authored/parsed directly as free text. Either signal marks an item free-text.
    """
    return bool(is_subjective) or (answer_type or "").upper() == "TEXT"
