"""Status notifications over Postgres LISTEN/NOTIFY (FR13, §6 SSE).

Status transitions happen in the *worker* process; the SSE stream is served by the *api*
process. Postgres LISTEN/NOTIFY bridges them with no extra infrastructure (we already run
Postgres). ``pg_notify`` runs inside the worker's transaction, so a notification is emitted
only if the state change actually commits — no phantom "done" events.

Single api instance fans out to all its SSE subscribers from one LISTEN connection. For
many api instances, each LISTENs independently — NOTIFY broadcasts to all. No code change.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Protocol

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def channel_for(portfolio_id: uuid.UUID) -> str:
    """LISTEN/NOTIFY channel name for a portfolio (identifier-safe)."""
    return f"portfolio_{portfolio_id.hex}"


class StatusNotifier(Protocol):
    async def publish(
        self, session: AsyncSession, *, portfolio_id: uuid.UUID, call_id: uuid.UUID, state: str
    ) -> None:
        ...


class NullNotifier:
    """No-op notifier (default for tests / when SSE isn't wired)."""

    async def publish(
        self, session: AsyncSession, *, portfolio_id: uuid.UUID, call_id: uuid.UUID, state: str
    ) -> None:
        return None


class PgNotifier:
    """Emits a NOTIFY in the caller's transaction (delivered on commit)."""

    async def publish(
        self, session: AsyncSession, *, portfolio_id: uuid.UUID, call_id: uuid.UUID, state: str
    ) -> None:
        payload = json.dumps({"call_id": str(call_id), "state": state})
        await session.execute(
            text("SELECT pg_notify(:chan, :payload)"),
            {"chan": channel_for(portfolio_id), "payload": payload},
        )


def _asyncpg_dsn(database_url: str) -> str:
    """SQLAlchemy URL (postgresql+asyncpg://...) → plain asyncpg DSN."""
    return database_url.replace("+asyncpg", "", 1)


async def subscribe(
    database_url: str, channel: str, *, stop: asyncio.Event | None = None
) -> AsyncIterator[str]:
    """Yield JSON payloads delivered to ``channel`` until cancelled/stopped.

    Opens a dedicated asyncpg connection (LISTEN needs its own connection, separate from
    the pooled request connections).
    """
    conn = await asyncpg.connect(_asyncpg_dsn(database_url))
    queue: asyncio.Queue[str] = asyncio.Queue()

    def _on_notify(_conn: object, _pid: int, _channel: str, payload: str) -> None:
        queue.put_nowait(payload)

    await conn.add_listener(channel, _on_notify)
    try:
        while stop is None or not stop.is_set():
            try:
                yield await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                yield ""  # keepalive tick (lets the SSE layer send a heartbeat / detect close)
    finally:
        await conn.remove_listener(channel, _on_notify)
        await conn.close()
