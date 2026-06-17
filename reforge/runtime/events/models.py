"""Execution Event Model — immutable semantic facts about runtime lifecycle.

ExecutionEvents capture *what happened and why* during execution:
which decisions were made, what failed, how recovery was applied.

Contrast with TraceEvents (observability/tracing layer) which capture
*who did what and when* (spans, timing, actor identity).  These two
layers are complementary and must remain independent:

    TraceEvent  → observability plane  (spans, timing, actor tracing)
    ExecutionEvent → semantic plane    (business facts, failure semantics)

Design principles:
  - Immutable frozen dataclass (never mutated after creation)
  - Self-contained payload (no object references, only primitives)
  - Factory functions enforce required payload shape per kind
  - FailureCategory + semantic_meaning are the foundation for
    runtime learning and replay analysis
  - Zero dependencies on any runtime subsystem (stdlib only)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Vocabulary types
# ---------------------------------------------------------------------------

EventKind = Literal[
    "EXECUTION_STARTED",
    "EXECUTION_SUCCEEDED",
    "EXECUTION_FAILED",
    "RECOVERY_ATTEMPTED",
    "EVALUATION_COMPLETED",
    "REFLECTION_GENERATED",
    "POLICY_DECIDED",
    "TASK_COMPLETED",
]

FailureCategory = Literal[
    "dependency",     # missing package / import error
    "syntax",         # code syntax / parse error
    "runtime_error",  # generic runtime exception
    "timeout",        # execution timed out
    "policy_blocked", # blocked by governor / capability policy
    "unknown",        # unclassified failure
]


# ---------------------------------------------------------------------------
# Core event type
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eid() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class ExecutionEvent:
    """An immutable fact about a runtime lifecycle transition.

    Use the factory functions below to construct events with the correct
    payload shape for each kind.  Direct construction is allowed but the
    factory functions enforce required keys.

    trace_id        — cross-session causal root (one user request -> one
                      trace_id, even when the runtime fans out into many
                      subtask sessions). Defaults to None so old callers
                      keep working; new callers thread `ExecutionContext`.
    parent_event_id — link to the immediately-causing event in the same trace,
                      e.g. RECOVERY_ATTEMPTED.parent_event_id == EXECUTION_FAILED.event_id.
    """

    kind: EventKind
    session_id: str
    event_id: str = field(default_factory=_eid)
    timestamp: str = field(default_factory=_now)
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    parent_event_id: str | None = None


# ---------------------------------------------------------------------------
# ExecutionContext — sealed carrier for trace propagation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionContext:
    """Read-only carrier threaded through nested execution.

    A single user request -> one ExecutionContext with a fresh trace_id.
    When the runtime fans out into a subtask, call `ctx.child(session_id=...)`
    to derive a child context that keeps the same trace_id but starts a new
    session_id, so the dashboard can pivot either by `trace_id` (whole
    request) or `session_id` (a single graph run).
    """

    trace_id: str
    session_id: str
    parent_event_id: str | None = None

    @classmethod
    def new(cls, session_id: str) -> "ExecutionContext":
        return cls(trace_id=uuid.uuid4().hex[:16], session_id=session_id)

    def child(
        self,
        session_id: str,
        parent_event_id: str | None = None,
    ) -> "ExecutionContext":
        """Derive a child context for a spawned subtask / parallel worker."""
        return ExecutionContext(
            trace_id=self.trace_id,
            session_id=session_id,
            parent_event_id=parent_event_id or self.parent_event_id,
        )


# ---------------------------------------------------------------------------
# Factory functions — enforce payload contract per kind
# ---------------------------------------------------------------------------


def execution_started(
    session_id: str,
    task: str,
    *,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        kind="EXECUTION_STARTED",
        session_id=session_id,
        payload={"task": task},
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )


def execution_succeeded(
    session_id: str,
    task: str,
    output_summary: str = "",
    *,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        kind="EXECUTION_SUCCEEDED",
        session_id=session_id,
        payload={"task": task, "output_summary": output_summary},
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )


def execution_failed(
    session_id: str,
    task: str,
    *,
    category: FailureCategory,
    recoverable: bool,
    error: str,
    semantic_meaning: str = "",
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> ExecutionEvent:
    """Construct an EXECUTION_FAILED event with full failure semantics."""
    return ExecutionEvent(
        kind="EXECUTION_FAILED",
        session_id=session_id,
        payload={
            "task": task,
            "category": category,
            "recoverable": recoverable,
            "error": error,
            "semantic_meaning": semantic_meaning,
        },
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )


def recovery_attempted(
    session_id: str,
    task: str,
    strategy: str,
    attempt: int,
    *,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        kind="RECOVERY_ATTEMPTED",
        session_id=session_id,
        payload={"task": task, "strategy": strategy, "attempt": attempt},
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )


def evaluation_completed(
    session_id: str,
    *,
    score: float,
    passed: bool,
    reasons: list[str] | None = None,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        kind="EVALUATION_COMPLETED",
        session_id=session_id,
        payload={"score": score, "passed": passed, "reasons": reasons or []},
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )


def reflection_generated(
    session_id: str,
    summary: str,
    *,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        kind="REFLECTION_GENERATED",
        session_id=session_id,
        payload={"summary": summary},
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )


def policy_decided(
    session_id: str,
    decision: str,
    reason: str,
    *,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        kind="POLICY_DECIDED",
        session_id=session_id,
        payload={"decision": decision, "reason": reason},
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )


def task_completed(
    session_id: str,
    outcome: str,
    reason: str,
    answer_summary: str = "",
    *,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> ExecutionEvent:
    """Emit once when final_response_node resolves the task outcome."""
    return ExecutionEvent(
        kind="TASK_COMPLETED",
        session_id=session_id,
        payload={
            "outcome": outcome,
            "reason": reason,
            "answer_summary": answer_summary,
        },
        trace_id=trace_id,
        parent_event_id=parent_event_id,
    )
