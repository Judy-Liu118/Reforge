"""Retry decision node — unified governor authority.

All retry / stop / accept decisions are resolved by ExecutionGovernor.
This node sets policy_reason and semantic context only.

Migrated fields (set by the emitter wrapper, not here):
  - control_state.retry_count       ← RECOVERY_ATTEMPTED event count
  - control_state.retry_decision_action ← POLICY_DECIDED event decision
"""

from __future__ import annotations

from typing import Literal

from reforge.config import config
from reforge.runtime.orchestration.governor import ExecutionGovernor
from reforge.runtime.domain.state.models import RuntimeState


def retry_decision_node(state: RuntimeState) -> dict:
    governor = ExecutionGovernor(max_retries=config.max_retry)
    resolution = governor.resolve(state)

    control_state = state.control_state.model_copy(
        update={"policy_reason": resolution.reason}
        # retry_decision_action and retry_count are set by wrap_retry_decision_node
    )
    semantic_state = state.semantic_state.model_copy(
        update={"task_intent": resolution.task_intent}
    )

    clf = {
        "intentional": resolution.intentional,
        "retryable": resolution.retryable,
        "failure_mode": resolution.failure_mode,
    }

    return {
        "classification_result": clf,
        "control_state": control_state,
        "semantic_state": semantic_state,
        "retry_decision": {"action": resolution.action, "reason": resolution.reason},
    }


def should_retry(
    state: RuntimeState,
) -> Literal["code_generation", "final_response"]:
    if state.control_state.retry_decision_action == "RETRY":
        return "code_generation"
    return "final_response"
