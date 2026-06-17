"""Runtime State Projection — derives current execution state from ExecutionEventLog.

This is the live-state complement to SessionReplay (which summarises completed
attempts).  project_state() answers "what is the current status of this session?"
by walking all events in session order and tracking the latest value of each
field.

Relationship to SessionSummary (replay.py):
  SessionSummary    → historical : per-attempt breakdown, final verdict
  RuntimeStateProjection (here) → live state : latest field values, real-time outcome

Design invariant:
  Fields derived from events should agree with corresponding RuntimeState fields.
  This module is the foundation for the event-sourced RuntimeState migration
  described in CLAUDE.md and DAILY_TASKS.md (LATER section).

Zero dependencies on runtime subsystems (stdlib + events/ only).
"""

from __future__ import annotations

from dataclasses import dataclass

from reforge.runtime.events.log import ExecutionEventLog


@dataclass(frozen=True)
class RuntimeStateProjection:
    """Read-only projection of execution state derived from events.

    All fields reflect the LATEST event of each kind seen in the session.
    Fields carried over from a previous attempt (e.g. last_failure_category)
    may be stale after a retry succeeds — check last_execution_outcome first.
    """

    session_id: str
    retry_count: int              # RECOVERY_ATTEMPTED event count
    current_attempt: int          # EXECUTION_STARTED event count
    last_execution_outcome: str   # "succeeded" | "failed" | "" (none yet)
    last_failure_category: str    # from latest EXECUTION_FAILED payload, or ""
    last_failure_semantic: str    # semantic_meaning from latest EXECUTION_FAILED
    last_eval_score: float        # from latest EVALUATION_COMPLETED
    last_eval_passed: bool        # from latest EVALUATION_COMPLETED
    last_reflection: str          # from latest REFLECTION_GENERATED
    last_policy_decision: str     # "RETRY" | "ACCEPT" | "STOP" | ""
    is_terminal: bool             # True when last decision is ACCEPT/STOP or task completed
    outcome: str                  # "succeeded" | "failed" | "in_progress"
    task_completed_outcome: str   # from TASK_COMPLETED payload, or "" (task not yet finished)


def project_state(session_id: str, event_log: ExecutionEventLog) -> RuntimeStateProjection:
    """Derive a RuntimeStateProjection for *session_id* from recorded events."""
    events = event_log.query(session_id=session_id)

    retry_count = 0
    current_attempt = 0
    last_execution_outcome = ""
    last_failure_category = ""
    last_failure_semantic = ""
    last_eval_score = 0.0
    last_eval_passed = False
    last_reflection = ""
    last_policy_decision = ""
    task_completed_outcome = ""

    for event in events:
        kind = event.kind
        p = event.payload

        if kind == "EXECUTION_STARTED":
            current_attempt += 1
        elif kind == "EXECUTION_SUCCEEDED":
            last_execution_outcome = "succeeded"
        elif kind == "EXECUTION_FAILED":
            last_execution_outcome = "failed"
            last_failure_category = str(p.get("category", ""))
            last_failure_semantic = str(p.get("semantic_meaning", ""))
        elif kind == "EVALUATION_COMPLETED":
            last_eval_score = float(p.get("score", 0.0))
            last_eval_passed = bool(p.get("passed", False))
        elif kind == "REFLECTION_GENERATED":
            last_reflection = str(p.get("summary", ""))
        elif kind == "POLICY_DECIDED":
            last_policy_decision = str(p.get("decision", ""))
        elif kind == "RECOVERY_ATTEMPTED":
            retry_count += 1
        elif kind == "TASK_COMPLETED":
            task_completed_outcome = str(p.get("outcome", ""))

    is_terminal = last_policy_decision in ("ACCEPT", "STOP") or bool(task_completed_outcome)

    _success_outcomes = {"SUCCESS", "RECOVERED", "EXPECTED_FAILURE"}
    if task_completed_outcome:
        outcome = "succeeded" if task_completed_outcome in _success_outcomes else "failed"
    elif last_policy_decision == "ACCEPT":
        outcome = "succeeded"
    elif last_policy_decision == "STOP":
        outcome = "failed"
    else:
        outcome = "in_progress"

    return RuntimeStateProjection(
        session_id=session_id,
        retry_count=retry_count,
        current_attempt=current_attempt,
        last_execution_outcome=last_execution_outcome,
        last_failure_category=last_failure_category,
        last_failure_semantic=last_failure_semantic,
        last_eval_score=last_eval_score,
        last_eval_passed=last_eval_passed,
        last_reflection=last_reflection,
        last_policy_decision=last_policy_decision,
        is_terminal=is_terminal,
        outcome=outcome,
        task_completed_outcome=task_completed_outcome,
    )
