"""Postgres LISTEN/NOTIFY status bridge (Phase 1 SSE backbone). DB-backed."""

from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest

from app.db import session_scope
from app.notifier import PgNotifier, channel_for

pytestmark = pytest.mark.usefixtures("db_ready")


async def test_publish_delivers_notify_to_listener(test_db_dsn: str) -> None:
    pid, call_id = uuid.uuid4(), uuid.uuid4()

    conn = await asyncpg.connect(test_db_dsn)
    received: asyncio.Queue[str] = asyncio.Queue()
    await conn.add_listener(channel_for(pid), lambda *a: received.put_nowait(a[3]))
    try:
        # NOTIFY is emitted on commit (session_scope commits at exit).
        async with session_scope() as s:
            await PgNotifier().publish(s, portfolio_id=pid, call_id=call_id, state="DONE")

        payload = await asyncio.wait_for(received.get(), timeout=5.0)
        data = json.loads(payload)
        assert data["state"] == "DONE"
        assert data["call_id"] == str(call_id)
    finally:
        await conn.remove_listener(channel_for(pid), lambda *a: None)
        await conn.close()


async def test_no_notify_on_other_channel(test_db_dsn: str) -> None:
    pid_a, pid_b = uuid.uuid4(), uuid.uuid4()

    conn = await asyncpg.connect(test_db_dsn)
    received: asyncio.Queue[str] = asyncio.Queue()
    await conn.add_listener(channel_for(pid_a), lambda *a: received.put_nowait(a[3]))
    try:
        async with session_scope() as s:
            await PgNotifier().publish(
                s, portfolio_id=pid_b, call_id=uuid.uuid4(), state="DONE"
            )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(received.get(), timeout=1.0)
    finally:
        await conn.close()
