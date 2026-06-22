"""Parse the Everest checklist plain-text format into builder items (FR6).

A strict parser for ONE specific layout — anything that doesn't fit raises ``ParseError`` so the
UI can fall back to the manual editor. The expected shape:

    Debt Collection Call Evaluation Checklist        <- optional title → checklist name
    A. Compliance and Mandatory Disclosures          <- section header ("<Letter>. <Title>")
    Recording Disclosure                             <- item title (label)
    Did the collector state the ... Disclosure?      <- item question → item text
    Notes: ...                                       <- optional guidance (Notes/Audit/Examples)
    Response: Yes / No / NA / Other                  <- optional → answer type + options
    Comment:                                         <- ends the item

Items with no ``Response:`` line become free-text (TEXT) items.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SECTION_RE = re.compile(r"^[A-Z]\.\s+(.+)$")
_NOTE_PREFIXES = ("notes:", "audit notes/reference:", "examples:", "note:", "reference:")


class ParseError(ValueError):
    """The text did not match the expected checklist format."""


@dataclass
class ParsedItem:
    section: str
    text: str
    answer_type: str          # PASS_FAIL | PASS_FAIL_NA | CHOICE | TEXT
    options: list[str] | None  # verbatim options for CHOICE; None otherwise
    is_subjective: bool
    risk: str
    guidance: str


def _classify(options: list[str]) -> tuple[str, list[str] | None, bool]:
    """Map a ``Response:`` option list to (answer_type, options, is_subjective)."""
    low = [o.lower() for o in options]
    has_yes_no = "yes" in low and "no" in low
    if has_yes_no and set(low) <= {"yes", "no", "na"}:
        # Plain compliance yes/no → objective, no stored options.
        return ("PASS_FAIL_NA" if "na" in low else "PASS_FAIL"), None, False
    # Qualitative or multi-choice (Strong/Average/…, Submissive/…, Yes/No/NA/Other, …).
    # Yes/No-based choices stay objective; purely qualitative scales are subjective.
    return "CHOICE", options, not has_yes_no


def parse_checklist(text: str) -> tuple[str | None, list[ParsedItem]]:
    """Parse the checklist text. Returns (name, items). Raises ParseError on a bad format."""
    name: str | None = None
    section: str | None = None
    items: list[ParsedItem] = []
    block: list[str] = []

    def flush() -> None:
        nonlocal block
        if not block:
            return
        if section is None:  # defensive — blocks only accumulate after a section header
            raise ParseError("checklist item appears before any section header")
        title = block[0].strip()
        question_parts: list[str] = []
        guidance_parts: list[str] = []
        options: list[str] | None = None
        for raw in block[1:]:
            s = raw.strip()
            if not s:
                continue
            low = s.lower()
            if low.startswith("response:"):
                options = [o.strip() for o in s.split(":", 1)[1].split("/") if o.strip()]
            elif any(low.startswith(p) for p in _NOTE_PREFIXES):
                guidance_parts.append(s.split(":", 1)[1].strip())
            elif low.startswith("comment:"):
                continue
            else:
                question_parts.append(s)
        item_text = " ".join(question_parts).strip() or title
        if not item_text:
            raise ParseError("a checklist item has no question text")
        if options is None:
            answer_type, opts, subjective = "TEXT", None, True
        else:
            answer_type, opts, subjective = _classify(options)
        items.append(
            ParsedItem(
                section=section,
                text=item_text,
                answer_type=answer_type,
                options=opts,
                is_subjective=subjective,
                risk="NORMAL",
                guidance=" ".join(guidance_parts).strip(),
            )
        )
        block = []

    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        m = _SECTION_RE.match(s)
        if m:
            flush()
            section = m.group(1).strip()
            continue
        if section is None:
            # Lines before the first section header: the first is the checklist name.
            if name is None:
                name = s
            continue
        block.append(s)
        if s.lower().startswith("comment:"):
            flush()
    flush()  # trailing item with no closing Comment line

    if not items:
        raise ParseError("no checklist items found — is this the expected format?")
    return name, items
