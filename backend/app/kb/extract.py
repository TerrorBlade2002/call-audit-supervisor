"""Extract plaintext from KB documents (PDF). Used to ground rubric distillation (§7.2)."""

from __future__ import annotations

import hashlib
import io

from pypdf import PdfReader


def extract_pdf_text(data: bytes) -> tuple[str, int]:
    """Return (plaintext, page_count) for a PDF byte string."""
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return text, len(reader.pages)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
