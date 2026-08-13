"""HTTP routers. Mounted in app.main as phases land."""

from __future__ import annotations

# Leading =, +, - or @ makes a spreadsheet treat a cell as a formula, so user-supplied text in a
# CSV export is prefixed with a quote to keep it inert. Applied to recording file names, which
# are the one CSV column whose value comes straight from the uploader's filesystem.
_CSV_FORMULA_LEADS = ("=", "+", "-", "@")


def csv_safe(value: str | None) -> str | None:
    """Neutralize a leading formula character in a CSV cell. None/empty pass through."""
    if not value:
        return value
    return f"'{value}" if value.startswith(_CSV_FORMULA_LEADS) else value
