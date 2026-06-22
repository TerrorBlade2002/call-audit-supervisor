"""Durable orchestration engine (§8).

A Postgres-backed queue + explicit state machine — no managed orchestrator. Layered:

  * states.py     — the state machine (pure): claimable/parked/terminal, legal transitions.
  * retry.py      — failure policy (pure): retry-with-backoff vs dead-letter.
  * stubs.py      — Phase-2 stub STT + judge (replaced in Phases 3/5).
  * handlers.py   — per-state step handlers producing a StepOutcome.
  * queue.py      — the durable queue repository (claim/transition/defer; raw SQL).
  * engine.py     — orchestrator: dispatch a claimed job, apply outcome, honour caps/limits.
  * reconciler.py — liveness sweep (lost-webhook + stuck-lease recovery).
"""
