"""API request/response models (Pydantic v2). Kept separate from ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

AnswerType = Literal["PASS_FAIL", "PASS_FAIL_NA", "CHOICE", "TEXT"]
RiskLevel = Literal["NORMAL", "ELEVATED", "CRITICAL"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth ---
class DevLoginRequest(BaseModel):
    email: EmailStr
    name: str | None = None
    as_admin: bool = False  # dev convenience: grant org ADMIN (super admin), non-production
    role: str | None = None  # dev: grant this portfolio role (SUPERVISOR/AGENT) on all portfolios


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    name: str
    status: str


# --- portfolios ---
class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class PortfolioOut(ORMModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    my_role: str | None = None  # caller's role here: ADMIN / SUPERVISOR / AGENT / …


class MemberAssign(BaseModel):
    user_id: uuid.UUID
    role: str = Field(description="ADMIN/MANAGER/ANALYST/VERIFIER/VIEWER")


# --- agents ---
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    external_ref: str | None = Field(default=None, max_length=200)


class AgentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AgentOut(ORMModel):
    id: uuid.UUID
    portfolio_id: uuid.UUID
    name: str
    external_ref: str | None
    created_at: datetime


# --- uploads / ingestion (Phase 1) ---
MAX_BATCH = 10  # NFR2: <=10 recordings/agent/batch


class PresignItemRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=400)


class PresignRequest(BaseModel):
    files: list[PresignItemRequest] = Field(min_length=1, max_length=MAX_BATCH)


class PresignItem(BaseModel):
    filename: str       # echo of the client's filename, to correlate the response
    key: str            # server-generated object key (sent back at register time)
    upload_url: str     # presigned PUT — browser uploads directly to R2 (FR3.2)


class PresignResponse(BaseModel):
    bucket: str
    expires_in: int
    uploads: list[PresignItem]


class CallRegisterItem(BaseModel):
    key: str = Field(min_length=1)
    duration_sec: int | None = Field(default=None, ge=0)


class CallRegisterRequest(BaseModel):
    items: list[CallRegisterItem] = Field(min_length=1, max_length=MAX_BATCH)


class CallOut(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    duration_sec: int | None
    batch_id: uuid.UUID | None
    status: str | None  # job state: PENDING_TRANSCRIPTION/AWAITING_TRANSCRIPT/.../DONE/FAILED
    last_error: str | None = None  # graceful failure reason when status == FAILED
    report_id: uuid.UUID | None  # present once judged
    option: str | None = None  # processing OPTION (FULL/RAW_ONLY/FEEDBACK_IDEAL/CHECKLIST_ONLY)
    created_at: datetime
    # when processing ended (DONE → report time, else FAILED time)
    completed_at: datetime | None = None


class CallRegisterResponse(BaseModel):
    batch_id: uuid.UUID
    calls: list[CallOut]


class UploadQuotaOut(BaseModel):
    """Per-portfolio in-flight upload headroom (NFR3): how many recordings can be queued now."""

    max: int
    in_flight: int
    remaining: int


# --- knowledge base (Phase 4) ---
class KbPresignRequest(BaseModel):
    files: list[PresignItemRequest] = Field(min_length=1, max_length=MAX_BATCH)


class DocumentRegisterItem(BaseModel):
    key: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    page_count: int | None = Field(default=None, ge=0, le=120)  # NFR1: <=120 pages


class DocumentRegisterRequest(BaseModel):
    items: list[DocumentRegisterItem] = Field(min_length=1, max_length=MAX_BATCH)


class DocumentOut(ORMModel):
    id: uuid.UUID
    filename: str | None = None
    page_count: int | None
    sha256: str
    created_at: datetime


# --- checklist builder (Phase 4) ---
class ChecklistItemIn(BaseModel):
    section: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    answer_type: AnswerType
    options: list[str] | None = None
    is_subjective: bool = False
    risk: RiskLevel = "NORMAL"
    guidance: str | None = None
    sort_order: int | None = None


class ChecklistItemOut(ORMModel):
    id: uuid.UUID
    section: str
    text: str
    answer_type: str
    options: list[str] | None
    is_subjective: bool
    risk: str
    guidance: str | None
    rubric_slice: str | None
    sort_order: int


class ChecklistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    items: list[ChecklistItemIn] = Field(min_length=1)
    requires_kb: bool = True  # call the KB when filling this checklist (§7.2)


class ChecklistUpdate(ChecklistCreate):
    pass


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ChecklistOut(ORMModel):
    id: uuid.UUID
    family_id: uuid.UUID
    name: str
    is_default: bool
    version: int
    status: str
    requires_kb: bool = True
    created_at: datetime
    updated_at: datetime | None = None


class ChecklistDetailOut(ChecklistOut):
    items: list[ChecklistItemOut]


class ParsedChecklistOut(BaseModel):
    """Result of parsing an uploaded checklist .txt — editable items for the builder to load."""

    name: str | None = None
    items: list[ChecklistItemIn]


# --- objection clustering (Phase 5) ---
class ObjectionClusterOut(BaseModel):
    representative_text: str
    count: int
    cleared_count: int
    never_cleared: bool
    examples: list[str]


class ObjectionLogOut(BaseModel):
    """One objection-log row — call id, agent, handled (pass/fail), upload time, and text."""

    call_id: uuid.UUID
    created_at: datetime
    text: str
    agent: str | None = None
    cleared: bool = False


class TranscriptLogOut(BaseModel):
    """One row of the append-only transcript log — call id + folder/agent + upload time."""

    call_id: uuid.UUID
    agent_name: str | None
    created_at: datetime


# --- auth + user management ---
class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)


class PortfolioUserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=6, max_length=200)
    role: str  # SUPERVISOR | AGENT


class PortfolioUserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str


# --- batch smoke summary ---
class AgentSnapshotOut(BaseModel):
    agent: str
    calls: int
    passes: int
    fails: int
    critical_fails: int
    calls_fail_gt_pass: int
    worst_report_id: uuid.UUID | None = None


class FailedItemOut(BaseModel):
    text: str
    section: str
    risk: str
    agents_failed: int
    calls_failed: int


class WorstCallOut(BaseModel):
    call_id: uuid.UUID
    report_id: uuid.UUID
    agent: str
    fails: int
    critical: int
    flagged: bool
    needs_review: bool


class ChecklistSummaryOut(BaseModel):
    total_calls: int
    agents: int
    clean: int
    need_review: int
    critical_fails: int
    failed_processing: int
    missing_agent_name: int
    per_agent: list[AgentSnapshotOut]
    top_failed_items: list[FailedItemOut]
    worst_calls: list[WorstCallOut]


# --- prompt builder (§B, super admin) ---
class AgentPromptOut(ORMModel):
    id: uuid.UUID
    agent: str
    portfolio_id: uuid.UUID | None = None  # binding scope: folder/portfolio/global (both null)
    agent_id: uuid.UUID | None = None
    name: str
    content: str
    in_use: bool
    created_at: datetime
    updated_at: datetime


class AgentPromptCreate(BaseModel):
    agent: str = Field(min_length=1, max_length=20)
    portfolio_id: uuid.UUID | None = None  # null = global/portfolio tier; with agent_id = folder
    agent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class ReportTemplateOut(ORMModel):
    id: uuid.UUID
    portfolio_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    name: str
    content: str
    in_use: bool
    created_at: datetime
    updated_at: datetime


class ReportTemplateCreate(BaseModel):
    portfolio_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class OutputSchemaOut(ORMModel):
    id: uuid.UUID
    agent: str
    portfolio_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    name: str
    content: dict[str, Any]
    in_use: bool
    created_at: datetime
    updated_at: datetime


class OutputSchemaCreate(BaseModel):
    agent: str = Field(min_length=1, max_length=20)
    portfolio_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    content: dict[str, Any]


# --- reports (Phase 6) ---
class ReportItemOut(BaseModel):
    id: uuid.UUID
    checklist_item_id: uuid.UUID
    section: str
    text: str
    answer: str | None          # normalized PASS / FAIL / NA (drives coloring)
    raw_answer: str | None      # verbatim option (Yes / Strong / Submissive / free text)
    options: list[str] | None   # the item's allowed answers
    answer_type: str | None = None   # CHOICE / PASS_FAIL / TEXT — drives free-text rendering
    is_subjective: bool = False      # free-text (qualitative) item — show raw_answer, not a badge
    confidence: float | None
    evidence_quote: str | None
    evidence_offset_sec: float | None
    comment: str | None
    decided_by: str | None
    needs_human_review: bool
    user_note: str | None


class ReportObjectionOut(BaseModel):
    text: str
    category: str | None
    cleared: bool


class ReportOut(BaseModel):
    id: uuid.UUID
    call_id: uuid.UUID
    checklist_id: uuid.UUID | None = None  # null for feedback-only (FEEDBACK_IDEAL) reports
    option: str | None = None  # the OPTION that produced this report (drives which sections show)
    agent_name: str | None = None   # extracted from the transcript (Agent 1)
    flagged_for_review: bool
    flag_reason: str | None
    narrative: dict[str, object] | None   # generated eagerly by the 3-agent pipeline
    items: list[ReportItemOut]
    objections: list[ReportObjectionOut]


class NoteUpdate(BaseModel):
    note: str = Field(max_length=5000)


class AgentNameUpdate(BaseModel):
    """Auditor override for the report's agent name (replaces the auto-extracted one).

    Stored durably on the call, so it survives re-processing. Empty string clears the override and
    reverts to the auto-extracted name."""

    agent_name: str = Field(max_length=200)


# --- verification (Phase 7) ---
Judgement = Literal["CORRECT", "WRONG", "CANT_SAY"]


class VerificationCreate(BaseModel):
    judgement: Judgement
    notes: str | None = Field(default=None, max_length=5000)


class VerificationOut(ORMModel):
    id: uuid.UUID
    report_id: uuid.UUID
    verifier_id: uuid.UUID
    judgement: str
    notes: str | None
    created_at: datetime


class DownloadUrlOut(BaseModel):
    url: str
    expires_in: int


class AgreementOut(BaseModel):
    total: int
    correct: int
    wrong: int
    cant_say: int
    agreement_rate: float | None   # correct / (correct + wrong); None if no decided verifications
