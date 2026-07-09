"""Memory write helper — extract MemoryRecord from a completed RuntimeState.

Shared by RuntimeRunner (automatic write-back) and CLI (display tag).
Returns None when the run is not worth persisting (intentional demo, etc.).
"""
from __future__ import annotations

from reforge.memory.models import MemoryRecord, should_persist_memory


def record_from_final_state(state: object, session_id: str) -> MemoryRecord | None:
    """Build a MemoryRecord from *state* after a run completes.

    Returns None when the outcome does not qualify for persistence
    (e.g. intentional failure accepted without recovery).

    *state* is typed as object to avoid a hard import of RuntimeState here;
    the function accesses only well-known attributes via getattr so it
    remains importable without pulling in the full runtime dependency tree.
    """
    ss = getattr(state, "semantic_state", None)
    cs = getattr(state, "control_state", None)
    os_ = getattr(state, "outcome_state", None)
    clf = getattr(state, "classification_result", None)
    attempts = getattr(state, "attempts", [])

    outcome = (os_.task_outcome if os_ else None) or "UNKNOWN"

    # Extract primary error type from attempts (first occurrence)
    primary_error = ""
    for a in attempts:
        if getattr(a, "error_type", ""):
            primary_error = a.error_type
            break

    # Reflection text and recovery actions from SemanticState
    reflection_text = (ss.reflection_summary if ss else None) or ""
    recovery_actions: list[str] = []
    rr = ss.reflection_result if ss else None
    if rr:
        reflection_text = reflection_text or getattr(rr, "error_summary", "")
        fix = getattr(rr, "suggested_fix", "")
        if fix:
            recovery_actions.append(fix)

    is_intentional = bool(clf.is_expected_failure) if clf else False
    requires_recovery = (bool(clf.retryable) if clf else False) and is_intentional
    retry_count = cs.retry_count if cs else 0
    decision_reason = (os_.outcome_reason if os_ else None) or ""
    traceback = getattr(state, "traceback", "")

    if not should_persist_memory(
        outcome=outcome,
        decision_reason=decision_reason,
        error_type=primary_error,
        retry_count=retry_count,
        is_intentional=is_intentional,
        requires_recovery=requires_recovery,
    ):
        return None

    return MemoryRecord.from_session(
        session_id=session_id,
        user_request=getattr(state, "user_request", ""),
        outcome=outcome,
        retry_count=retry_count,
        error_type=primary_error,
        reflection_summary=reflection_text,
        recovery_action="; ".join(recovery_actions),
        traceback=traceback,
    )


def execution_record_from_final_state(state: object) -> dict | None:
    """Build ExecutionMemory.record() kwargs from a completed RuntimeState.

    This is the write side of the governor's repair recall: ClassifyStage
    calls ExecutionMemory.recall_similar() with the current failure's
    fingerprint, so something has to persist (signature → repair that
    worked) pairs. Only RECOVERED sessions qualify — a record without a
    proven repair carries no hint value and would shadow useful records
    in the top-3 recall window.

    Returns None when the session doesn't qualify (no failure snapshot,
    not recovered, or reflection produced no concrete fix).
    """
    ss = getattr(state, "semantic_state", None)
    os_ = getattr(state, "outcome_state", None)
    snapshot = getattr(ss, "last_failure", None) if ss else None

    outcome = (os_.task_outcome if os_ else None) or ""
    if outcome != "RECOVERED" or snapshot is None:
        return None
    suggested_fix = getattr(snapshot, "suggested_fix", "")
    if not suggested_fix:
        return None

    return {
        "request": getattr(state, "user_request", ""),
        "outcome": outcome,
        "failure_mode": getattr(snapshot, "failure_mode", "") or "execution_error",
        "retryable": True,
        "repair_strategy": suggested_fix,
        "task_intent": (ss.task_intent if ss else None) or "",
        "problem_signature": getattr(snapshot, "problem_signature", {}) or {},
        "error_type": getattr(snapshot, "error_type", ""),
    }
