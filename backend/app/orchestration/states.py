"""The job state machine (§8.1) — pure logic, no I/O.

```
PENDING_TRANSCRIPTION --submit--> AWAITING_TRANSCRIPT --webhook/reconciler--> PENDING_JUDGE
PENDING_JUDGE --report--> DONE
(PENDING_TRANSCRIPTION | PENDING_JUDGE) --attempts exhausted--> FAILED
```

``AWAITING_TRANSCRIPT`` is **parked**: the worker loop never claims it; only the webhook
or the reconciler advances it. This is the rule that keeps idle workers from burning
billing while a transcript is in flight.
"""

from __future__ import annotations

from app.models import JobState

# Claimed and worked by the durable loop.
CLAIMABLE: frozenset[JobState] = frozenset(
    {JobState.PENDING_TRANSCRIPTION, JobState.PENDING_JUDGE}
)
# Advanced only by webhook/reconciler, never the loop.
PARKED: frozenset[JobState] = frozenset({JobState.AWAITING_TRANSCRIPT})
# End states.
TERMINAL: frozenset[JobState] = frozenset({JobState.DONE, JobState.FAILED})

# Legal transitions. Anything not listed is a bug and is rejected by ``assert_transition``.
_ALLOWED: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING_TRANSCRIPTION: frozenset(
        {JobState.AWAITING_TRANSCRIPT, JobState.FAILED}
    ),
    JobState.AWAITING_TRANSCRIPT: frozenset({JobState.PENDING_JUDGE, JobState.FAILED}),
    JobState.PENDING_JUDGE: frozenset({JobState.DONE, JobState.FAILED}),
}


def is_claimable(state: JobState) -> bool:
    return state in CLAIMABLE


def is_terminal(state: JobState) -> bool:
    return state in TERMINAL


def can_transition(src: JobState, dst: JobState) -> bool:
    return dst in _ALLOWED.get(src, frozenset())


def assert_transition(src: JobState, dst: JobState) -> None:
    if not can_transition(src, dst):
        raise ValueError(f"illegal transition {src} -> {dst}")
