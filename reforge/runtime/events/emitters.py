"""Node-level event emitter wrappers for the runtime graph.

Each `wrap_*_node` function takes an existing graph node callable and returns
a new callable with identical signature that additionally emits ExecutionEvents
to the provided log.  When `event_log` is None, the original function is
returned unchanged (zero overhead, full backward compatibility).

Causal threading: when a `trace_id` is provided, every emitted event carries
that trace_id, and `parent_event_id` is set to the immediately-preceding
event in the same session (best-effort causal chain via log query). When
`trace_id` is None, both fields stay None — preserving the historic default.

Design principles:
  - Exceptions from the wrapped node propagate unchanged
  - event_log=None → identity wrapper, not a NOP wrapper
  - Migrated fields (events are source of truth): after emitting, the
    corresponding state-update value is overridden with the event-derived value.
      retry_count, retry_decision_action  (wrap_retry_decision_node)
      eval_score, eval_passed             (wrap_evaluation_node)
      reflection_summary                  (wrap_reflection_node)
      task_outcome, outcome_reason        (wrap_final_response_node)

Note on architecture: this module resides in the events/ package but imports
RuntimeState from the state layer to type-annotate NodeFn.  This is an
intentional exception to the "stdlib only" rule; emitters are a graph bridge
layer, not part of the events model itself.
"""

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


def _last_event_id(
    event_log: ExecutionEventLog,
    session_id: str,
    trace_id: str | None,
) -> str | None:
    """Return the most recent event_id in *session_id*, or None if no trace is active.

    When `trace_id` is None, callers do not want parent links populated either
    (backwards-compat mode) — so short-circuit to None without querying.
    """
    if trace_id is None:
        return None
    events = event_log.query(session_id=session_id)
    return events[-1].event_id if events else None


def wrap_final_response_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
    *,
    trace_id: str | None = None,
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
        outcome_state = result.get("outcome_state")
        if outcome_state is not None:
            if isinstance(outcome_state, dict):
                outcome = str(outcome_state.get("task_outcome") or "")
                reason = str(outcome_state.get("outcome_reason") or "")
                answer = str(outcome_state.get("final_answer") or "")
            else:
                outcome = str(getattr(outcome_state, "task_outcome", "") or "")
                reason = str(getattr(outcome_state, "outcome_reason", "") or "")
                answer = str(getattr(outcome_state, "final_answer", "") or "")
            event_log.append(
                task_completed(
                    session_id,
                    outcome,
                    reason,
                    answer[:200],
                    trace_id=trace_id,
                    parent_event_id=_last_event_id(event_log, session_id, trace_id),
                )
            )
        return result

    return wrapped


def wrap_execution_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
    *,
    trace_id: str | None = None,
) -> NodeFn:
    """Emit EXECUTION_STARTED + EXECUTION_SUCCEEDED/FAILED around the execution node."""
    if event_log is None:
        return node_fn

    def wrapped(state: RuntimeState) -> dict:
        task = state.user_request[:80]
        event_log.append(
            execution_started(
                session_id,
                task,
                trace_id=trace_id,
                parent_event_id=_last_event_id(event_log, session_id, trace_id),
            )
        )
        result = node_fn(state)
        exec_state = result.get("exec_state")
        exit_code = exec_state.exit_code if exec_state is not None else None
        if exit_code is None or exit_code == 0:
            event_log.append(
                execution_succeeded(
                    session_id,
                    task,
                    trace_id=trace_id,
                    parent_event_id=_last_event_id(event_log, session_id, trace_id),
                )
            )
        else:
            stderr = exec_state.stderr if exec_state is not None else ""
            category, meaning = categorize_failure(exit_code, stderr)
            event_log.append(
                execution_failed(
                    session_id,
                    task,
                    category=category,
                    recoverable=True,
                    error=stderr[:300],
                    semantic_meaning=meaning,
                    trace_id=trace_id,
                    parent_event_id=_last_event_id(event_log, session_id, trace_id),
                )
            )
        return result

    return wrapped


def wrap_evaluation_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
    *,
    trace_id: str | None = None,
) -> NodeFn:
    """Emit EVALUATION_COMPLETED after the evaluation node.

    Migrated fields: score and passed in evaluation_result are overridden with
    the values emitted to the event log (events are source of truth).
    """
    if event_log is None:
        return node_fn

    def wrapped(state: RuntimeState) -> dict:
        result = node_fn(state)
        evaluation_update = result.get("evaluation_result") or {}
        if isinstance(evaluation_update, dict):
            score = float(evaluation_update.get("score", 0.0))
            passed = bool(evaluation_update.get("passed", False))
            failure_type = evaluation_update.get("failure_type", "")
        else:
            score = float(getattr(evaluation_update, "score", 0.0))
            passed = bool(getattr(evaluation_update, "passed", False))
            failure_type = getattr(evaluation_update, "failure_type", "")
        reasons = [failure_type] if failure_type else []
        event_log.append(
            evaluation_completed(
                session_id,
                score=score,
                passed=passed,
                reasons=reasons,
                trace_id=trace_id,
                parent_event_id=_last_event_id(event_log, session_id, trace_id),
            )
        )

        # Mirror score/passed into the legacy evaluation_result key.
        evaluation_value = result.get("evaluation_result")
        if isinstance(evaluation_value, dict):
            evaluation_value = {**evaluation_value, "score": score, "passed": passed}
            result = {**result, "evaluation_result": evaluation_value}
        elif evaluation_value is not None and hasattr(evaluation_value, "model_copy"):
            evaluation_value = evaluation_value.model_copy(
                update={"score": score, "passed": passed}
            )
            result = {**result, "evaluation_result": evaluation_value}

        # Mirror into semantic_state.evaluation_result so nested-state consumers
        # see the same values.
        semantic_base = result.get("semantic_state") or state.semantic_state
        if evaluation_value is None:
            return result

        if isinstance(evaluation_value, dict):
            evaluation_model = EvaluationResult.model_validate(evaluation_value)
        elif hasattr(evaluation_value, "model_copy"):
            evaluation_model = evaluation_value
        else:
            evaluation_model = None

        if evaluation_model is None:
            return result

        if hasattr(semantic_base, "model_copy"):
            updated_semantic = semantic_base.model_copy(
                update={"evaluation_result": evaluation_model}
            )
        elif isinstance(semantic_base, dict):
            updated_semantic = {**semantic_base, "evaluation_result": evaluation_model}
        else:
            updated_semantic = semantic_base
        return {**result, "semantic_state": updated_semantic}

    return wrapped


def wrap_reflection_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
    *,
    trace_id: str | None = None,
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
        reflection = result.get("reflection_result") or {}
        summary = (
            reflection.get("error_summary", "")
            if isinstance(reflection, dict)
            else getattr(reflection, "error_summary", "")
        )
        if summary:
            event_log.append(
                reflection_generated(
                    session_id,
                    summary,
                    trace_id=trace_id,
                    parent_event_id=_last_event_id(event_log, session_id, trace_id),
                )
            )
            semantic_state = result.get("semantic_state")
            if semantic_state is not None:
                updated_semantic = (
                    semantic_state.model_copy(update={"reflection_summary": summary})
                    if hasattr(semantic_state, "model_copy")
                    else {**semantic_state, "reflection_summary": summary}
                    if isinstance(semantic_state, dict)
                    else semantic_state
                )
                result = {**result, "semantic_state": updated_semantic}
        return result

    return wrapped


def wrap_retry_decision_node(
    node_fn: NodeFn,
    event_log: ExecutionEventLog | None,
    session_id: str,
    *,
    trace_id: str | None = None,
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
        retry_decision = result.get("retry_decision") or {}
        action_str = str(
            retry_decision.get("action", "") if isinstance(retry_decision, dict)
            else getattr(retry_decision, "action", "")
        )
        reason = (
            retry_decision.get("reason", "") if isinstance(retry_decision, dict)
            else getattr(retry_decision, "reason", "")
        )
        event_log.append(
            policy_decided(
                session_id,
                action_str,
                reason,
                trace_id=trace_id,
                parent_event_id=_last_event_id(event_log, session_id, trace_id),
            )
        )

        # retry_decision_action mirrors every POLICY_DECIDED.
        # retry_count mirrors the cumulative RECOVERY_ATTEMPTED count on RETRY.
        control_updates: dict = {"retry_decision_action": action_str}
        if action_str == "RETRY":
            attempt = state.control_state.retry_count + 1
            # Anchor RECOVERY_ATTEMPTED to the EXECUTION_FAILED that triggered
            # the retry, not the POLICY_DECIDED we just emitted — that makes
            # the cause/effect link in the trace tree.
            recovery_parent: str | None = None
            if trace_id is not None:
                failures = event_log.query(
                    kind="EXECUTION_FAILED", session_id=session_id
                )
                recovery_parent = failures[-1].event_id if failures else None
            event_log.append(
                recovery_attempted(
                    session_id,
                    state.user_request[:80],
                    strategy="llm_retry",
                    attempt=attempt,
                    trace_id=trace_id,
                    parent_event_id=recovery_parent,
                )
            )
            control_updates["retry_count"] = len(
                event_log.query(kind="RECOVERY_ATTEMPTED", session_id=session_id)
            )

        control_state = result.get("control_state")
        if control_state is not None:
            updated_control_state = (
                control_state.model_copy(update=control_updates)
                if hasattr(control_state, "model_copy")
                else {**control_state, **control_updates}
                if isinstance(control_state, dict)
                else control_state
            )
            result = {**result, "control_state": updated_control_state}
        return result

    return wrapped
