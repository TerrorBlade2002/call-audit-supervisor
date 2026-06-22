"""SSE status stream (FR13, §6). One stream per portfolio, RBAC-scoped.

Streams job status transitions (uploaded → transcribing → judging → done/failed) pushed by
the worker via Postgres NOTIFY. The browser opens an EventSource per visible portfolio.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from app.authz import AuthContext, authorize
from app.config import settings
from app.notifier import channel_for, subscribe
from app.rbac import Action

router = APIRouter(tags=["events"])


@router.get("/portfolios/{pid}/events")
async def portfolio_events(
    pid: uuid.UUID,
    _ctx: Annotated[AuthContext, Depends(authorize(Action.AGENT_VIEW))],
) -> EventSourceResponse:
    channel = channel_for(pid)

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        async for payload in subscribe(settings.database_url, channel):
            if payload:
                yield {"event": "status", "data": payload}
            else:
                yield {"event": "ping", "data": ""}  # keepalive

    return EventSourceResponse(event_stream())
