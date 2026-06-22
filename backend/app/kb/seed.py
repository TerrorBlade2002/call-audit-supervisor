"""Default knowledge base shipped per portfolio (FR5): the Everest operational documents.

Bundled as extracted plaintext (the source PDFs live in the org's drive). On portfolio
creation these become ``documents`` rows whose ``text`` grounds rubric distillation (§7.2).
Managers add/replace KB docs via the upload endpoint, which re-triggers distillation.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.extract import sha256_hex
from app.models import Document

_SEED_DIR = Path(__file__).parent / "seed_text"

DEFAULT_KB_DOCS: list[dict[str, object]] = [
    {"file": "everest_guidebook.txt", "title": "Everest Debt Collection Guidebook", "pages": 45},
    {"file": "astra_call_structure.txt", "title": "Astra Call Structure & AI Prompts", "pages": 34},
]


async def seed_default_kb(session: AsyncSession, portfolio_id: uuid.UUID) -> int:
    """Insert the default Everest KB documents for a new portfolio. Returns count."""
    count = 0
    for doc in DEFAULT_KB_DOCS:
        path = _SEED_DIR / str(doc["file"])
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        session.add(
            Document(
                portfolio_id=portfolio_id,
                r2_uri=f"seed://{doc['file']}",
                page_count=int(doc["pages"]),  # type: ignore[call-overload]
                sha256=sha256_hex(text),
                text=text,
            )
        )
        count += 1
    await session.flush()
    return count
