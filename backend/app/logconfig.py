"""Process-wide stdio hardening.

structlog's default logger ``print()``s to stdout. On Windows that stream defaults to the
locale codec (cp1252), so a single non-ASCII character in a log line (an em-dash in a
checklist name, a "→" in a model message) raises UnicodeEncodeError and takes the whole
worker down. Forcing UTF-8 with ``errors="replace"`` makes logging unkillable by content.
"""

from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # e.g. pytest capture object — leave it alone
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
