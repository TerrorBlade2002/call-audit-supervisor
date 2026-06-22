"""RBAC permission matrix (§2). ADMIN-everything + per-role scoping."""

from __future__ import annotations

import pytest

from app.rbac import Action, Role, is_allowed


@pytest.mark.parametrize("action", list(Action))
def test_admin_allowed_everything(action: Action) -> None:
    assert is_allowed(Role.ADMIN, action) is True


def test_only_admin_creates_or_deletes_portfolios() -> None:
    for role in (Role.MANAGER, Role.ANALYST, Role.VERIFIER, Role.VIEWER):
        assert is_allowed(role, Action.PORTFOLIO_CREATE) is False
        assert is_allowed(role, Action.PORTFOLIO_DELETE) is False
        assert is_allowed(role, Action.USER_MANAGE) is False


def test_manager_manages_kb_and_checklists() -> None:
    assert is_allowed(Role.MANAGER, Action.KB_MANAGE) is True
    assert is_allowed(Role.MANAGER, Action.CHECKLIST_MANAGE) is True
    # Analyst can create agents + upload but not manage KB/checklists.
    assert is_allowed(Role.ANALYST, Action.AGENT_CREATE) is True
    assert is_allowed(Role.ANALYST, Action.RECORDING_UPLOAD) is True
    assert is_allowed(Role.ANALYST, Action.KB_MANAGE) is False
    assert is_allowed(Role.ANALYST, Action.CHECKLIST_MANAGE) is False


def test_verifier_capabilities() -> None:
    assert is_allowed(Role.VERIFIER, Action.VERIFICATION_SUBMIT) is True
    assert is_allowed(Role.VERIFIER, Action.RECORDING_DOWNLOAD) is True
    assert is_allowed(Role.VERIFIER, Action.REPORT_VIEW) is True
    # Verifier may not upload recordings or create agents.
    assert is_allowed(Role.VERIFIER, Action.RECORDING_UPLOAD) is False
    assert is_allowed(Role.VERIFIER, Action.AGENT_CREATE) is False


def test_viewer_is_read_only() -> None:
    assert is_allowed(Role.VIEWER, Action.REPORT_VIEW) is True
    assert is_allowed(Role.VIEWER, Action.PORTFOLIO_VIEW) is True
    # Objections + insight tools are a supervisor-level surface — a plain read-only viewer
    # (like an agent) sees reports only, not the cross-call objection log.
    assert is_allowed(Role.VIEWER, Action.OBJECTIONS_VIEW) is False
    assert is_allowed(Role.VIEWER, Action.INSIGHTS_VIEW) is False
    for forbidden in (
        Action.RECORDING_UPLOAD,
        Action.AGENT_CREATE,
        Action.VERIFICATION_SUBMIT,
        Action.REPORT_NOTE,
        Action.KB_MANAGE,
    ):
        assert is_allowed(Role.VIEWER, forbidden) is False


def test_only_verifier_submits_verifications() -> None:
    for role in (Role.MANAGER, Role.ANALYST, Role.VIEWER):
        assert is_allowed(role, Action.VERIFICATION_SUBMIT) is False
