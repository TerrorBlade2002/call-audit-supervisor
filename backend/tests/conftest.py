"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.db as appdb
from app.config import settings
from app.models import Base


# Tests run against a SEPARATE database so a test run can NEVER touch the app's data.
# Derived from DATABASE_URL by swapping the db name (override with TEST_DATABASE_URL), and
# created on demand. Truncating happens only here, never in the app's database.
def _test_db_url() -> str:
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return override
    base, _, _db = settings.database_url.rpartition("/")
    return f"{base}/everest_pytest"


_TEST_DB_URL = _test_db_url()


async def _ensure_test_db() -> None:
    """Create the dedicated test database if it doesn't exist (app DB used as maintenance).

    Uses SQLAlchemy's connection handling (the same engine config the tests use) with
    AUTOCOMMIT (CREATE DATABASE can't run inside a transaction). Best-effort: if the DB server
    isn't reachable yet, the DB-backed tests skip themselves via the db_ready fixture.
    """
    target = _TEST_DB_URL.rpartition("/")[2].split("?")[0]
    eng = create_async_engine(
        settings.database_url, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with eng.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{target}"'))
    except Exception:  # noqa: BLE001 — server down → DB-backed tests skip themselves
        pass
    finally:
        await eng.dispose()


def pytest_configure(config: pytest.Config) -> None:
    try:
        asyncio.run(_ensure_test_db())
    except Exception:  # noqa: BLE001
        pass


# pytest-asyncio gives each test its own event loop, but the app's module-global engine
# pools connections bound to the loop that created them → "Event loop is closed" on reuse.
# Point the app at a NullPool engine for tests: a fresh connection per use, no cross-loop
# reuse. (Production keeps the pooled engine — this only affects the test process.)
_test_engine = create_async_engine(_TEST_DB_URL, poolclass=NullPool)
_test_sessionmaker = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _use_nullpool_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appdb, "engine", _test_engine)
    monkeypatch.setattr(appdb, "SessionLocal", _test_sessionmaker)


@pytest.fixture
def test_db_dsn() -> str:
    """Plain libpq DSN for the dedicated test DB — for raw asyncpg connections (e.g. LISTEN).

    Tests must listen on the SAME database the app session publishes to, since Postgres
    NOTIFY is per-database; using settings.database_url here would listen on the wrong DB.
    """
    return _TEST_DB_URL.replace("+asyncpg", "", 1)


@pytest.fixture(autouse=True)
def _no_external_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic tests: never let an endpoint build a real Gemini/AssemblyAI client from a
    .env key (it would make a live API call). Endpoints fall back to stubs; unit tests that
    exercise the real clients construct them explicitly with mock transports."""
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "assemblyai_api_key", "")

# DB-unavailable signals → skip. Deliberately NOT catching ProgrammingError etc., so a
# genuine schema bug still fails the build (in CI, where Postgres is reachable).
_DB_UNAVAILABLE = (
    OSError,
    ConnectionError,
    OperationalError,
    InterfaceError,
    asyncpg.exceptions.InvalidAuthorizationSpecificationError,
    asyncpg.exceptions.CannotConnectNowError,
)

_SEED_ROLES = (
    "INSERT INTO roles (name) VALUES "
    "('ADMIN'),('SUPERVISOR'),('AGENT'),('MANAGER'),('ANALYST'),('VERIFIER'),('VIEWER') "
    "ON CONFLICT (name) DO NOTHING"
)
# Truncating users + portfolios CASCADEs to all dependents (agents, calls, jobs, reports,
# memberships, ...). Roles are kept — they're seed data.
_MUTABLE_TABLES = "users, portfolios"


async def _setup_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(_SEED_ROLES))
        await conn.execute(text(f"TRUNCATE {_MUTABLE_TABLES} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """DB-backed ASGI client. Skips the test if Postgres is unreachable.

    Creates the schema (idempotent) + seeds roles, truncates mutable tables for a clean
    slate, then yields an httpx client bound to the FastAPI app (which uses its own
    engine against the same database).
    """
    from app.main import app

    engine = create_async_engine(_TEST_DB_URL)
    try:
        await _setup_schema(engine)
    except _DB_UNAVAILABLE as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not available: {exc}")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest_asyncio.fixture
async def db_ready() -> AsyncIterator[None]:
    """Ensure schema + clean tables for DB-backed orchestration tests; skip if no DB.

    Tests using this fixture talk to the database via ``app.db.session_scope`` directly.
    """
    engine = create_async_engine(_TEST_DB_URL)
    try:
        await _setup_schema(engine)
    except _DB_UNAVAILABLE as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not available: {exc}")
    await engine.dispose()
    yield


class FakeClock:
    """Deterministic virtual clock. ``sleep`` advances time instead of blocking.

    Lets us test token-bucket waits and backoff sleeps without real delays.
    """

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
