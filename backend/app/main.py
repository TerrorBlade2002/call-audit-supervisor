"""FastAPI api service entrypoint.

Phase 0 surface: health checks + app shell. Routers (portfolios, agents, uploads,
checklists, reports, verification, webhooks, SSE) are mounted here as later phases land.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.admin import router as admin_router
from app.api.agents import router as agents_router
from app.api.auth import router as auth_router
from app.api.calls import router as calls_router
from app.api.checklists import router as checklists_router
from app.api.events import router as events_router
from app.api.kb import router as kb_router
from app.api.objections import router as objections_router
from app.api.output_schemas import router as output_schemas_router
from app.api.portfolios import router as portfolios_router
from app.api.prompts import router as prompts_router
from app.api.report_templates import router as report_templates_router
from app.api.reports import router as reports_router
from app.api.summaries import router as summaries_router
from app.api.transcripts import router as transcripts_router
from app.api.uploads import router as uploads_router
from app.api.verification import router as verification_router
from app.api.webhooks import router as webhooks_router
from app.config import settings
from app.db import engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Only runs under a real server (uvicorn); ASGITransport in tests skips lifespan.
    from app.logconfig import force_utf8_stdio

    force_utf8_stdio()  # Windows cp1252 stdout would crash on non-ASCII log content.
    yield


app = FastAPI(title="Everest Auditor API", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness — process is up. Cheap, no dependencies."""
    return {"status": "ok", "env": settings.env}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    """Readiness — can reach Postgres. Used by the platform before routing traffic."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ready"}


app.include_router(auth_router)
app.include_router(portfolios_router)
app.include_router(agents_router)
app.include_router(uploads_router)
app.include_router(calls_router)
app.include_router(events_router)
app.include_router(webhooks_router)
app.include_router(kb_router)
app.include_router(checklists_router)
app.include_router(objections_router)
app.include_router(transcripts_router)
app.include_router(prompts_router)
app.include_router(report_templates_router)
app.include_router(output_schemas_router)
app.include_router(summaries_router)
app.include_router(reports_router)
app.include_router(verification_router)
app.include_router(admin_router)
