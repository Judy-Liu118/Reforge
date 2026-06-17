"""Node-level event emitter wrappers for the runtime graph.

Each `wrap_*_node` function takes an existing graph node callable and returns
a new callable with identical signature that additionally emits ExecutionEvents
to the provided log.  When `event_log` is None, the original function is
returned unchanged (zero overhead, full backward compatibility).

Design principles:
  - Exceptions from the wrapped node propagate unchanged
  - event_log=None → identity wrapper, not a NOP wrapper
  - Migrated fields: after emitting, the corresponding state-update value is
    overridden with the event-derived value so ExecutionEventLog is source of
    truth.  Migrated fields (as of P38):
      retry_count, retry_decision_action  (wrap_retry_decision_node)
      eval_score, eval_passed             (wrap_evaluation_node)
      reflection_summary                  (wrap_reflection_node)
      task_outcome, outcome_reason        (wrap_final_response_node)

Note on architecture: this module resides in the events/ package but imports
RuntimeState from the state layer to type-annotate NodeFn.  This is an
intentional exception to the "stdlib only" rule; emitters are a graph bridge
layer, not part of the events model itself.
"""

from __future__ import annotations

from typing import Callable

from reforge.runtime.events.categorizer import categorize_failure
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import (
    evaluation_completed,
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    recovery_attempted,
    reflection_generated,
    task_completed,
)
from reforge.runtime.domain.state.models import EvaluationResult, RuntimeState

NodeFn = Callable[[RuntimeState], dict]


def wrap_final_response_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
) -> NodeFn:
    """Emit TASK_COMPLETED after the final response node resolves the outcome.

    Captures outcome / reason / first 200 chars of final_answer from the
    outcome_state returned by the node.  Emitted once per session, at the
    very end of the lifecycle — after POLICY_DECIDED.
    """
    if event_log is None:
        return node_fn

    def wrapped(state: RuntimeState) -> dict:
        result = node_fn(state)
        os_result = result.get("outcome_state")
        if os_result is not None:
            if isinstance(os_result, dict):
                outcome = str(os_result.get("task_outcome") or "")
                reason = str(os_result.get("outcome_reason") or "")
                answer = str(os_result.get("final_answer") or "")
            else:
                outcome = str(getattr(os_result, "task_outcome", "") or "")
                reason = str(getattr(os_result, "outcome_reason", "") or "")
                answer = str(getattr(os_result, "final_answer", "") or "")
            event_log.append(
                task_completed(session_id, outcome, reason, answer[:200])
            )
        return result

    return wrapped


def wrap_execution_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
) -> NodeFn:
    """Emit EXECUTION_STARTED + EXECUTION_SUCCEEDED/FAILED around the execution node."""
    if event_log is None:
        return node_fn

    def wrapped(state: RuntimeState) -> dict:
        task = state.user_request[:80]
        event_log.append(execution_started(session_id, task))
        result = node_fn(state)
        es = result.get("exec_state")
        exit_code = es.exit_code if es is not None else None
        if exit_code is None or exit_code == 0:
            event_log.append(execution_succeeded(session_id, task))
        else:
            stderr = es.stderr if es is not None else ""
            cat, meaning = categorize_failure(exit_code, stderr)
            event_log.append(
                execution_failed(
                    session_id,
                    task,
                    category=cat,
                    recoverable=True,
                    error=stderr[:300],
                    semantic_meaning=meaning,
                )
            )
        return result

    return wrapped


def wrap_evaluation_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
) -> NodeFn:
    """Emit EVALUATION_COMPLETED after the evaluation node.

    Migrated fields: score and passed in evaluation_result are overridden with
    the values emitted to the event log (events are source of truth).
    """
    if event_log is None:
        return node_fn

    def wrapped(state: RuntimeState) -> dict:
        result = node_fn(state)
        ev = result.get("evaluation_result") or {}
        if isinstance(ev, dict):
            score = float(ev.get("score", 0.0))
            passed = bool(ev.get("passed", False))
            ft = ev.get("failure_type", "")
            reasons = [ft] if ft else []
        else:
            score = float(getattr(ev, "score", 0.0))
            passed = bool(getattr(ev, "passed", False))
            ft = getattr(ev, "failure_type", "")
            reasons = [ft] if ft else []
        event_log.append(
            evaluation_completed(session_id, score=score, passed=passed, reasons=reasons)
        )
        # Override evaluation_result legacy key (kept so legacy nodes still work).
        ev_in_result = result.get("evaluation_result")
        if isinstance(ev_in_result, dict):
            ev_in_result = {**ev_in_result, "score": score, "passed": passed}
            result = {**result, "evaluation_result": ev_in_result}
        elif ev_in_result is not None and hasattr(ev_in_result, "model_copy"):
            ev_in_result = ev_in_result.model_copy(update={"score": score, "passed": passed})
            result = {**result, "evaluation_result": ev_in_result}

        # Canonical path (P42): embed into semantic_state.evaluation_result.
        # Base on result["semantic_state"] if present, otherwise current state.semantic_state.
        sem_base = result.get("semantic_state") or state.semantic_state
        if ev_in_result is not None:
            er_model = (
                EvaluationResult.model_validate(ev_in_result)
                if isinstance(ev_in_result, dict)
                else ev_in_result
                if hasattr(ev_in_result, "model_copy")
                else None
            )
            if er_model is not None:
                result = {
                    **result,
                    "semantic_state": (
                        sem_base.model_copy(update={"evaluation_result": er_model})
                        if hasattr(sem_base, "model_copy")
                        else {**sem_base, "evaluation_result": er_model}
                        if isinstance(sem_base, dict) else sem_base
                    ),
                }
        return result

    return wrapped


def wrap_reflection_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
) -> NodeFn:
    """Emit REFLECTION_GENERATED whenever the node produces a non-empty summary.

    Migrated field: semantic_state.reflection_summary is overridden with the
    summary emitted to the event log (events are source of truth).

    Both failure and success reflections are captured so that the event log
    always reflects the latest reflection state across retry sequences.
    """
    if event_log is None:
        return node_fn

    def wrapped(state: RuntimeState) -> dict:
        result = node_fn(state)
        refl = result.get("reflection_result") or {}
        summary = (
            refl.get("error_summary", "")
            if isinstance(refl, dict)
            else getattr(refl, "error_summary", "")
        )
        if summary:
            event_log.append(reflection_generated(session_id, summary))
            # Override semantic_state.reflection_summary with event-derived value
            sem = result.get("semantic_state")
            if sem is not None:
                updated_sem = (
                    sem.model_copy(update={"reflection_summary": summary})
                    if hasattr(sem, "model_copy")
                    else {**sem, "reflection_summary": summary}
                    if isinstance(sem, dict)
                    else sem
                )
                result = {**result, "semantic_state": updated_sem}
        return result

    return wrapped


def wrap_retry_decision_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
) -> NodeFn:
    """Emit POLICY_DECIDED (+ RECOVERY_ATTEMPTED when retrying) after governor.

    Migrated fields: retry_decision_action and (on RETRY) retry_count in
    control_state are overridden with event-derived values so that
    ExecutionEventLog is the source of truth for both fields.
    """
    if event_log is None:
        return node_fn

    def wrapped(state: RuntimeState) -> dict:
        result = node_fn(state)
        rd = result.get("retry_decision") or {}
        action_str = str(
            rd.get("action", "") if isinstance(rd, dict)
            else getattr(rd, "action", "")
        )
        reason = (
            rd.get("reason", "") if isinstance(rd, dict)
            else getattr(rd, "reason", "")
        )
        event_log.append(policy_decided(session_id, action_str, reason))

        # Build event-derived overrides for control_state.
        # retry_decision_action is always overridden (mirrors POLICY_DECIDED).
        # retry_count is overridden on RETRY (mirrors RECOVERY_ATTEMPTED count).
        cs_updates: dict = {"retry_decision_action": action_str}
        if action_str == "RETRY":
            attempt = state.control_state.retry_count + 1
            event_log.append(
                recovery_attempted(
                    session_id,
                    state.user_request[:80],
                    strategy="llm_retry",
                    attempt=attempt,
                )
            )
            cs_updates["retry_count"] = len(
                event_log.query(kind="RECOVERY_ATTEMPTED", session_id=session_id)
            )

        cs = result.get("control_state")
        if cs is not None:
            updated_cs = (
                cs.model_copy(update=cs_updates)
                if hasattr(cs, "model_copy")
                else {**cs, **cs_updates}
                if isinstance(cs, dict)
                else cs
            )
            result = {**result, "control_state": updated_cs}
        return result

    return wrapped
