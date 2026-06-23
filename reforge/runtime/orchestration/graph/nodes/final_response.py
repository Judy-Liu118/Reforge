"""Final response node — assemble answer + resolve task outcome."""

from __future__ import annotations

from reforge.runtime.orchestration.outcome_resolver import resolve_outcome
from reforge.runtime.domain.state.models import RuntimeState


def _determine_task_outcome(state: RuntimeState) -> tuple[str, str]:
    er = state.semantic_state.evaluation_result
    eo = state.execution_output

    outcome, reason = resolve_outcome(
        task_intent=state.semantic_state.task_intent,
        execution_exit_code=eo.exit_code if eo else -1,
        retry_count=state.control_state.retry_count,
        eval_passed=er.passed if er else True,
        policy_action=state.control_state.retry_decision_action or "",
    )
    return (outcome.value, reason)


def final_response_node(state: RuntimeState) -> dict:
    cap = state.capability_decision

    if cap:
        deny_category = cap.get("deny_category", "capability_policy")
        outcome_state_denied = state.outcome_state.model_copy(
            update={
                "task_outcome": "DENIED",
                "outcome_reason": deny_category,
                "final_answer": f"Request denied: {deny_category}",
            }
        )
        return {"outcome_state": outcome_state_denied}

    if state.execution_output and state.execution_output.exit_code == 0:
        answer = state.execution_output.stdout
    else:
        stdout = state.execution_output.stdout if state.execution_output else ""
        tb = state.traceback
        parts = [f"Execution failed after {state.control_state.retry_count} retries."]
        if stdout:
            parts.append(f"\n--- stdout ---\n{stdout}")
        if tb:
            parts.append(f"\n--- stderr ---\n{tb}")
        answer = "\n".join(parts)

    task_outcome, outcome_reason = _determine_task_outcome(state)
    outcome_state = state.outcome_state.model_copy(
        update={
            "task_outcome": task_outcome,
            "outcome_reason": outcome_reason,
            "final_answer": answer,
        }
    )
    return {"outcome_state": outcome_state}
