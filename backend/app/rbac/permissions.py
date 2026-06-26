"""The single source of truth for authorization (§2).

Capabilities are expressed as (Action) -> {roles permitted}. ADMIN is omitted from the
matrix because it is granted *everything* (org-wide superset of MANAGER rights). The
middleware resolves a user's role *in the target portfolio*, then calls ``is_allowed``.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"  # org-wide super admin; implicitly all permissions, every portfolio
    SUPERVISOR = "SUPERVISOR"  # portfolio: full control except delete-portfolio / org admin
    AGENT = "AGENT"  # portfolio: read-only — view reports only
    # Legacy roles (kept for back-compat; not surfaced in the 2-role-per-portfolio model).
    MANAGER = "MANAGER"
    ANALYST = "ANALYST"
    VERIFIER = "VERIFIER"
    VIEWER = "VIEWER"


class Action(StrEnum):
    # Org / portfolio lifecycle
    PORTFOLIO_CREATE = "portfolio.create"
    PORTFOLIO_DELETE = "portfolio.delete"
    PORTFOLIO_VIEW = "portfolio.view"
    USER_MANAGE = "user.manage"          # create users, assign roles
    # Agents
    AGENT_CREATE = "agent.create"
    AGENT_VIEW = "agent.view"
    AGENT_MANAGE = "agent.manage"  # rename / remove an agent entity
    # Knowledge base
    KB_MANAGE = "kb.manage"
    KB_VIEW = "kb.view"
    # Checklists
    CHECKLIST_MANAGE = "checklist.manage"  # create / edit (append-only versioning) / rename
    CHECKLIST_DELETE = "checklist.delete"  # ADMIN only — supervisors are append-only (no delete)
    CHECKLIST_VIEW = "checklist.view"
    # Recordings / reports
    RECORDING_UPLOAD = "recording.upload"
    RECORDING_DOWNLOAD = "recording.download"
    RECORDING_DELETE = "recording.delete"
    REPORT_VIEW = "report.view"
    REPORT_NOTE = "report.note"          # edit per-item user notes
    OBJECTIONS_VIEW = "objections.view"
    # Portfolio insight tools (objection log, transcripts, summaries, CSV exports). Supervisor+;
    # NOT agents — agents are strictly view-a-report-only.
    INSIGHTS_VIEW = "insights.view"
    # Verification
    VERIFICATION_SUBMIT = "verification.submit"


# (Action) -> roles permitted, EXCLUDING ADMIN (ADMIN is always allowed).
# Derived directly from the §2 capability table.
# SUPERVISOR = full control of their portfolio (everything except delete-portfolio, which is
# super-admin-only). AGENT = read-only: may view the portfolio, its agents and reports, nothing
# else (no upload/delete, no checklist/KB, no notes). ADMIN (super admin) bypasses the matrix.
_SUP = Role.SUPERVISOR
_MATRIX: dict[Action, frozenset[Role]] = {
    Action.PORTFOLIO_CREATE: frozenset(),                       # ADMIN only
    Action.PORTFOLIO_DELETE: frozenset(),                       # ADMIN only (super admin)
    Action.USER_MANAGE: frozenset(),                            # ADMIN only
    Action.PORTFOLIO_VIEW: frozenset(
        {_SUP, Role.AGENT, Role.MANAGER, Role.ANALYST, Role.VERIFIER, Role.VIEWER}
    ),
    Action.AGENT_CREATE: frozenset({_SUP, Role.MANAGER, Role.ANALYST}),
    Action.AGENT_VIEW: frozenset(
        {_SUP, Role.AGENT, Role.MANAGER, Role.ANALYST, Role.VERIFIER, Role.VIEWER}
    ),
    Action.AGENT_MANAGE: frozenset({_SUP, Role.MANAGER}),       # rename/remove agents
    Action.KB_MANAGE: frozenset({_SUP, Role.MANAGER}),
    Action.KB_VIEW: frozenset({_SUP, Role.MANAGER, Role.ANALYST, Role.VERIFIER, Role.VIEWER}),
    Action.CHECKLIST_MANAGE: frozenset({_SUP, Role.MANAGER}),
    # Deleting a checklist is ADMIN-only (super admin). Supervisors are append-only and will
    # request deletion via a future review queue (see checklist-deletion-queue).
    Action.CHECKLIST_DELETE: frozenset(),
    Action.CHECKLIST_VIEW: frozenset(
        {_SUP, Role.MANAGER, Role.ANALYST, Role.VERIFIER, Role.VIEWER}
    ),
    Action.RECORDING_UPLOAD: frozenset({_SUP, Role.MANAGER, Role.ANALYST}),
    # Agents may download the recording/report of a call they can already view.
    Action.RECORDING_DOWNLOAD: frozenset({_SUP, Role.AGENT, Role.MANAGER, Role.VERIFIER}),
    Action.RECORDING_DELETE: frozenset({_SUP, Role.MANAGER}),   # destructive
    Action.REPORT_VIEW: frozenset(
        {_SUP, Role.AGENT, Role.MANAGER, Role.ANALYST, Role.VERIFIER, Role.VIEWER}
    ),
    Action.REPORT_NOTE: frozenset({_SUP, Role.MANAGER, Role.ANALYST, Role.VERIFIER}),
    # Insight tools + objection views are supervisor-level — NOT agents.
    Action.OBJECTIONS_VIEW: frozenset({_SUP, Role.MANAGER, Role.ANALYST, Role.VERIFIER}),
    Action.INSIGHTS_VIEW: frozenset({_SUP, Role.MANAGER, Role.ANALYST, Role.VERIFIER}),
    Action.VERIFICATION_SUBMIT: frozenset({_SUP, Role.VERIFIER}),
}


def is_allowed(role: Role, action: Action) -> bool:
    """Return whether ``role`` may perform ``action``. ADMIN is allowed everything."""
    if role is Role.ADMIN:
        return True
    return role in _MATRIX.get(action, frozenset())
