"""Job state machine (§8.1) — pure transitions."""

from __future__ import annotations

import pytest

from app.models import JobState
from app.orchestration import states


def test_claimable_excludes_parked_and_terminal() -> None:
    assert states.is_claimable(JobState.PENDING_TRANSCRIPTION)
    assert states.is_claimable(JobState.PENDING_JUDGE)
    # The parked state must never be claimed by the loop (§8.1).
    assert not states.is_claimable(JobState.AWAITING_TRANSCRIPT)
    assert not states.is_claimable(JobState.DONE)
    assert not states.is_claimable(JobState.FAILED)


def test_terminal_states() -> None:
    assert states.is_terminal(JobState.DONE)
    assert states.is_terminal(JobState.FAILED)
    assert not states.is_terminal(JobState.PENDING_JUDGE)


def test_legal_transitions() -> None:
    assert states.can_transition(JobState.PENDING_TRANSCRIPTION, JobState.AWAITING_TRANSCRIPT)
    assert states.can_transition(JobState.AWAITING_TRANSCRIPT, JobState.PENDING_JUDGE)
    assert states.can_transition(JobState.PENDING_JUDGE, JobState.DONE)
    assert states.can_transition(JobState.PENDING_TRANSCRIPTION, JobState.FAILED)
    assert states.can_transition(JobState.PENDING_JUDGE, JobState.FAILED)


def test_illegal_transitions_rejected() -> None:
    # Can't skip transcription straight to DONE, or resurrect a terminal state.
    assert not states.can_transition(JobState.PENDING_TRANSCRIPTION, JobState.DONE)
    assert not states.can_transition(JobState.PENDING_TRANSCRIPTION, JobState.PENDING_JUDGE)
    assert not states.can_transition(JobState.DONE, JobState.PENDING_JUDGE)
    with pytest.raises(ValueError):
        states.assert_transition(JobState.PENDING_TRANSCRIPTION, JobState.DONE)
