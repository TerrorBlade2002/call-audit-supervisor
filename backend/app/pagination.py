"""Offset pagination shared by list endpoints (bounded payloads → less load, faster UI).

Backwards compatible by design: endpoints still return a plain JSON array, and the page
metadata rides in response headers (``X-Total-Count`` / ``X-Limit`` / ``X-Offset``). Clients
that ignore the headers see the same array shape as before; the SPA reads them to drive
prev/next controls.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query, Response

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True)
class Page:
    limit: int
    offset: int

    @staticmethod
    def params(
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        offset: int = Query(0, ge=0),
    ) -> Page:
        return Page(limit=limit, offset=offset)


def set_page_headers(response: Response, total: int, page: Page) -> None:
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page.limit)
    response.headers["X-Offset"] = str(page.offset)
    # So the browser can read the counts even when the SPA is served cross-origin.
    response.headers["Access-Control-Expose-Headers"] = (
        "X-Total-Count, X-Limit, X-Offset, X-Is-Org-Admin"
    )
