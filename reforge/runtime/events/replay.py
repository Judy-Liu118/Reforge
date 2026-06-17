"""Session Replay — project ExecutionEventLog into structured session summaries.

This is the read-side of the event-sourced architecture introduced in P22/P23.
Given an ExecutionEventLog, SessionReplay can reconstruct what happened during
any session without touching RuntimeState.

Projection algorithm:
  Walk events in session order.  EXECUTION_STARTED opens a new attempt bucket;
  subsequent events (EXECUTION_SUCCEEDED/FAILED, EVALUATION_COMPLETED,
  REFLECTION_GENERATED, POLICY_DECIDED) annotate it.  POLICY_DECIDED closes
  the bucket and starts a new one if the decision is RETRY.

Session outcome:
  last attempt POLICY_DECIDED == "ACCEPT"  → "succeeded"
  last attempt POLICY_DECIDED == "STOP"    → "failed"
  no terminal POLICY_DECIDED yet           → "in_progress"

Isolation: zero dependencies on LangGraph, LLM, or RuntimeState.
"""

from __future__ import annotations

from dataclasses import dataclass

from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import ExecutionEvent


# ---------------------------------------------------------------------------
# Summary types (frozen — these are facts about the past)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptSummary:
    """Projection of a single execution attempt derived from events."""

    attempt_number: int
    execution_outcome: str   # "succeeded" | "failed" | "unknown"
    failure_category: str    # FailureCategory value, or "" on success
    semantic_meaning: str
    error_summary: str
    eval_score: float
    eval_passed: bool
    reflection_summary: str
    policy_decision: str     # "RETRY" | "ACCEPT" | "STOP" | "" if not yet decided


@dataclass(frozen=True)
class SessionSummary:
    """Full projection of a single session derived from its events."""

    session_id: str
    total_attempts: int
    final_outcome: str       # "succeeded" | "failed" | "in_progress"
    recovery_count: int      # RECOVERY_ATTEMPTED events seen
    attempts: tuple[AttemptSummary, ...]


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------


class SessionReplay:
    """Reconstruct session history from an ExecutionEventLog."""

    def __init__(self, log: ExecutionEventLog) -> None:
        self._log = log

    def summarize(self, session_id: str) -> SessionSummary:
        """Build a SessionSummary for *session_id* from recorded events."""
        events = self._log.query(session_id=session_id)
        return _build_summary(session_id, events)

    def all_summaries(self) -> list[SessionSummary]:
        """Return summaries for every session in the log, sorted by session_id."""
        return [self.summarize(sid) for sid in sorted(self._log.sessions())]

    def render(self, session_id: str) -> str:
        """Return a human-readable text timeline for *session_id*."""
        return render_summary(self.summarize(session_id))


# ---------------------------------------------------------------------------
# Projection logic
# ---------------------------------------------------------------------------


def _build_summary(
    session_id: str, events: list[ExecutionEvent]
) -> SessionSummary:
    attempts: list[AttemptSummary] = []
    current: dict | None = None
    recovery_count = 0

    for event in events:
        if event.kind == "EXECUTION_STARTED":
            current = {
                "attempt_number": len(attempts) + 1,
                "execution_outcome": "unknown",
                "failure_category": "",
                "semantic_meaning": "",
                "error_summary": "",
                "eval_score": 0.0,
                "eval_passed": False,
                "reflection_summary": "",
                "policy_decision": "",
            }

        elif event.kind == "EXECUTION_SUCCEEDED" and current is not None:
            current["execution_outcome"] = "succeeded"

        elif event.kind == "EXECUTION_FAILED" and current is not None:
            p = event.payload
            current["execution_outcome"] = "failed"
            current["failure_category"] = p.get("category", "")
            current["semantic_meaning"] = p.get("semantic_meaning", "")
            current["error_summary"] = str(p.get("error", ""))[:120]

        elif event.kind == "EVALUATION_COMPLETED" and current is not None:
            p = event.payload
            current["eval_score"] = float(p.get("score", 0.0))
            current["eval_passed"] = bool(p.get("passed", False))

        elif event.kind == "REFLECTION_GENERATED" and current is not None:
            current["reflection_summary"] = str(event.payload.get("summary", ""))

        elif event.kind == "POLICY_DECIDED" and current is not None:
            current["policy_decision"] = str(event.payload.get("decision", ""))
            attempts.append(AttemptSummary(**current))
            current = None

        elif event.kind == "RECOVERY_ATTEMPTED":
            recovery_count += 1

    # Incomplete attempt (no POLICY_DECIDED recorded yet)
    if current is not None:
        attempts.append(AttemptSummary(**current))

    # Determine final outcome from the last closed attempt
    last_decision = attempts[-1].policy_decision if attempts else ""
    if last_decision == "ACCEPT":
        final_outcome = "succeeded"
    elif last_decision == "STOP":
        final_outcome = "failed"
    else:
        final_outcome = "in_progress"

    return SessionSummary(
        session_id=session_id,
        total_attempts=len(attempts),
        final_outcome=final_outcome,
        recovery_count=recovery_count,
        attempts=tuple(attempts),
    )


# ---------------------------------------------------------------------------
# Text renderer
# ---------------------------------------------------------------------------


def render_summary(summary: SessionSummary) -> str:
    """Render *summary* as an indented text timeline."""
    header_parts = [
        f"Session: {summary.session_id}",
        f"Attempts: {summary.total_attempts}",
        f"Outcome: {summary.final_outcome}",
    ]
    if summary.recovery_count:
        header_parts.append(f"Recoveries: {summary.recovery_count}")
    lines = [" | ".join(header_parts)]

    for a in summary.attempts:
        tag = a.execution_outcome.upper()
        if a.failure_category:
            tag += f" — {a.failure_category}"
            if a.semantic_meaning:
                tag += f" / {a.semantic_meaning}"
        lines.append(f"\n  Attempt {a.attempt_number} [{tag}]")

        if a.eval_score or not a.eval_passed:
            status = "passed" if a.eval_passed else "failed"
            lines.append(f"    Eval: {a.eval_score:.2f} ({status})")

        if a.reflection_summary:
            lines.append(f"    Reflection: {a.reflection_summary[:80]}")

        if a.policy_decision:
            lines.append(f"    Policy: {a.policy_decision}")

    return "\n".join(lines)
