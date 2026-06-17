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
    clf = getattr(state, "classification_result", None) or {}
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

    is_intentional = bool(clf.get("intentional", False))
    requires_recovery = bool(clf.get("retryable", False)) and is_intentional
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
