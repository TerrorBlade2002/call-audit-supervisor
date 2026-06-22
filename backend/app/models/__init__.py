"""SQLAlchemy ORM models — the §9 data model.

Conventions:
  * UUID primary keys (server-generated via ``gen_random_uuid()``).
  * ``timezone=True`` timestamps, server-default now().
  * The ``jobs`` table doubles as the durable queue + workflow state (§8).
  * ``objections.embedding`` is a pgvector column for clustering (§7.5).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 768  # Gemini embedding size (§9 objections.embedding vector(768))


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ─────────────────────────────────────────────────────────────── identity / RBAC ──


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    password_hash: Mapped[str | None] = mapped_column(String(255))  # pbkdf2; null = no password
    created_at: Mapped[datetime] = _created_at()


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # ADMIN/...


class OrgMember(Base):
    """Org-wide role grant (ADMIN). Scopes a role to a user across the whole org.

    Distinct from ``portfolio_members`` (portfolio-scoped). The authorization resolver
    consults org grants first: an ADMIN here is allowed everything, everywhere (§2).
    """

    __tablename__ = "org_members"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), primary_key=True)


class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()


class PortfolioMember(Base):
    """Scopes a role to a (user, portfolio). The RBAC join table."""

    __tablename__ = "portfolio_members"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = _pk()
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()


# ──────────────────────────────────────────────────────────── KB / checklists ──


class Document(Base):
    """Knowledge-base document (retained; not subject to 30-day lifecycle)."""

    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _pk()
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    r2_uri: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(400))  # original name, for display
    page_count: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Extracted plaintext, used to ground rubric distillation (§7.2). Null until extracted.
    text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Checklist(Base):
    __tablename__ = "checklists"
    id: Mapped[uuid.UUID] = _pk()
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Groups all versions of one logical checklist (the `version` field distinguishes them).
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # status: draft | active | archived (archived = superseded by a newer version)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # When false, the judge fills this checklist WITHOUT the KB (no distillation, leaner
    # prompt) — for generic checklists answerable from the model's own knowledge (§7.2).
    requires_kb: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = _created_at()
    # Python-side default/onupdate so the value is populated in-process (avoids an async
    # lazy-load on a server-onupdate column that's expired right after flush).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    items: Mapped[list[ChecklistItem]] = relationship(
        back_populates="checklist", cascade="all, delete-orphan"
    )


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    id: Mapped[uuid.UUID] = _pk()
    checklist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checklists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # answer_type: PASS_FAIL / PASS_FAIL_NA / TEXT
    answer_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_subjective: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # risk: NORMAL / ELEVATED / CRITICAL
    risk: Mapped[str] = mapped_column(String(20), nullable=False, default="NORMAL")
    guidance: Mapped[str | None] = mapped_column(Text)
    rubric_slice: Mapped[str | None] = mapped_column(Text)  # distilled per §7.2
    # Display answer options for this item (e.g. ["Yes","No","NA"], a scale, or []=free text).
    # The engine still normalizes to PASS/FAIL/NA; these are the verbatim labels shown.
    options: Mapped[list[str] | None] = mapped_column(JSONB)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    checklist: Mapped[Checklist] = relationship(back_populates="items")


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    id: Mapped[uuid.UUID] = _pk()
    checklist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checklists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kb_sha256: Mapped[str | None] = mapped_column(String(64))  # change-detection (§7.2)
    distilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ──────────────────────────────────────────────────────── calls / jobs / queue ──


class JobState(enum.StrEnum):
    PENDING_TRANSCRIPTION = "PENDING_TRANSCRIPTION"
    AWAITING_TRANSCRIPT = "AWAITING_TRANSCRIPT"  # parked; only webhook/reconciler advances
    PENDING_JUDGE = "PENDING_JUDGE"
    DONE = "DONE"
    FAILED = "FAILED"


# Claimable by the worker loop. AWAITING_TRANSCRIPT is intentionally excluded (§8.1).
CLAIMABLE_STATES: tuple[str, ...] = (
    JobState.PENDING_TRANSCRIPTION.value,
    JobState.PENDING_JUDGE.value,
)


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[uuid.UUID] = _pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    r2_audio_uri: Mapped[str] = mapped_column(Text, nullable=False)
    r2_transcript_uri: Mapped[str | None] = mapped_column(Text)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Processing OPTION chosen at upload (FULL/RAW_ONLY/FEEDBACK_IDEAL/CHECKLIST_ONLY) and the
    # checklist + KB docs selected for it. checklist_id null = the portfolio's default checklist;
    # kb_doc_ids null = all of the portfolio's KB docs (the default set).
    option: Mapped[str] = mapped_column(
        String(20), nullable=False, default="FULL", server_default="FULL"
    )
    checklist_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checklists.id"))
    kb_doc_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class Job(Base):
    """Durable queue row + workflow state machine (§8). One job per call."""

    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _pk()
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # server_default so raw-SQL inserts (queue.enqueue) don't have to set these.
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    transcript_id: Mapped[str | None] = mapped_column(String(100), index=True)  # AAI idempotency
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class JobError(Base):
    """Full failure record for a job step — the traceback for debugging.

    ``jobs.last_error`` keeps only the short human reason; this table keeps one row per
    failed attempt with the stage, exception class, message, and complete traceback so
    failures are queryable without scraping worker logs. Cascade-deleted with the call/job.
    """

    __tablename__ = "job_errors"
    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)  # JobState at failure
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    fatal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_class: Mapped[str] = mapped_column(String(150), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


# ───────────────────────────────────────────────────────────────── reports ──


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = _pk()
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # Nullable: feedback-only (FEEDBACK_IDEAL) reports have no checklist.
    checklist_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checklists.id"))
    # The OPTION that produced this report — drives which merged-template sections render.
    option: Mapped[str | None] = mapped_column(String(20))
    model_passes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # raw model output snapshot
    agent_name: Mapped[str | None] = mapped_column(String(200))  # extracted from the transcript
    flagged_for_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flag_reason: Mapped[str | None] = mapped_column(Text)
    # narrative: lazy (§7.4); null until generated on report open
    narrative: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()

    items: Mapped[list[ReportItem]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ReportItem(Base):
    __tablename__ = "report_items"
    id: Mapped[uuid.UUID] = _pk()
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    checklist_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checklist_items.id"), nullable=False
    )
    answer: Mapped[str | None] = mapped_column(String(20))  # normalized PASS/FAIL/NA
    # verbatim model answer — usually a short option (Yes/Strong/…) but can be a full
    # sentence for subjective items, so Text (a fixed varchar truncates → judge INSERT fails).
    raw_answer: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    evidence_offset_sec: Mapped[float | None] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(40))  # tier name / "human"
    needs_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_note: Mapped[str | None] = mapped_column(Text)

    report: Mapped[Report] = relationship(back_populates="items")


class Objection(Base):
    __tablename__ = "objections"
    id: Mapped[uuid.UUID] = _pk()
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    cleared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


class Verification(Base):
    __tablename__ = "verifications"
    id: Mapped[uuid.UUID] = _pk()
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    verifier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    judgement: Mapped[str] = mapped_column(String(20), nullable=False)  # CORRECT/WRONG/CANT_SAY
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    __table_args__ = (
        CheckConstraint(
            "judgement IN ('CORRECT','WRONG','CANT_SAY')", name="ck_verification_judgement"
        ),
    )


class RouterOverride(Base):
    """Learned force-escalation per checklist item (§16.3)."""

    __tablename__ = "router_overrides"
    checklist_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="CASCADE"), primary_key=True
    )
    reason: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[datetime] = _created_at()


class DailyUsage(Base):
    """Per-portfolio, per-day submission counter for the daily cap (§8.6)."""

    __tablename__ = "daily_usage"
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls_submitted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = _pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ts: Mapped[datetime] = _created_at()
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AgentPrompt(Base):
    """A super-admin-authored prompt body for a judge agent (feedback/checklist/ideal/merged).

    The agent's system instruction = this body (when in_use) + the code-owned output directive +
    impartiality clause. A partial unique index enforces AT MOST ONE in_use row per agent, so
    activation is race-safe. No in_use row → the agent falls back to its hard-coded default body.
    """

    __tablename__ = "agent_prompts"
    id: Mapped[uuid.UUID] = _pk()
    agent: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # Binding scope: a prompt is bound to a folder (agent_id), a whole portfolio (agent_id NULL),
    # or globally (both NULL). Resolution is most-specific-first (see app/judge/scope.py).
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # markdown prompt body
    in_use: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_agent_prompt_inuse", "portfolio_id", "agent_id", "agent", unique=True,
            postgresql_where=text("in_use"), postgresql_nulls_not_distinct=True,
        ),
    )


class ReportTemplate(Base):
    """A super-admin-authored HTML report layout (logic-less template), bound per (portfolio,
    folder). The report is rendered deterministically from the structured report data using the
    in_use template for the call's (portfolio, folder); falls back to the built-in renderer when
    none. Same scope + single-in_use semantics as AgentPrompt.
    """

    __tablename__ = "report_templates"
    id: Mapped[uuid.UUID] = _pk()
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # the HTML template body
    in_use: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_report_template_inuse", "portfolio_id", "agent_id", unique=True,
            postgresql_where=text("in_use"), postgresql_nulls_not_distinct=True,
        ),
    )


class OutputSchema(Base):
    """A super-admin-authored Structured-Output JSON schema for a judge stage, bound per
    (portfolio, folder). Used as Gemini's response schema so the model output is deterministic
    and shaped by the admin; the system still extracts the operational core (verdicts/objections/
    feedback), validated at upload. No in_use row → the stage uses its built-in Pydantic schema.
    """

    __tablename__ = "output_schemas"
    id: Mapped[uuid.UUID] = _pk()
    agent: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # judge stage
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # the JSON schema
    in_use: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_output_schema_inuse", "portfolio_id", "agent_id", "agent", unique=True,
            postgresql_where=text("in_use"), postgresql_nulls_not_distinct=True,
        ),
    )


__all__ = [
    "Base",
    "JobState",
    "CLAIMABLE_STATES",
    "EMBEDDING_DIM",
    "User",
    "Role",
    "Portfolio",
    "PortfolioMember",
    "OrgMember",
    "Agent",
    "Document",
    "Checklist",
    "ChecklistItem",
    "RubricVersion",
    "Call",
    "Job",
    "Report",
    "ReportItem",
    "Objection",
    "Verification",
    "RouterOverride",
    "DailyUsage",
    "AuditLog",
    "AgentPrompt",
    "ReportTemplate",
    "OutputSchema",
]
