"""Retry decision node — unified governor authority.

All retry / stop / accept decisions are resolved by ExecutionGovernor.
This node sets policy_reason and semantic context only.

Migrated fields (set by the emitter wrapper, not here):
  - control_state.retry_count       ← RECOVERY_ATTEMPTED event count
  - control_state.retry_decision_action ← POLICY_DECIDED event decision

Ablation mode (REFORGE_GOVERNOR_BYPASS=1):
  Replaces the governor pipeline with a naive while-retry baseline:
  exit_code != 0 → RETRY until max_retries, otherwise ACCEPT. No failure
  classification, no intent, no capability check. Used to isolate the
  governor's contribution in controlled ablation experiments.
"""

from __future__ import annotations

import os
from typing import Literal

from reforge.config import config
from reforge.runtime.orchestration.governor import (
    ExecutionGovernor,
    RuntimeResolution,
)
from reforge.runtime.domain.state.models import RuntimeState


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(name: str) -> bool:
    """Case-insensitive boolean parse for an environment variable."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _is_bypass_enabled() -> bool:
    return _env_truthy("REFORGE_GOVERNOR_BYPASS")


def _naive_resolution(state: RuntimeState) -> RuntimeResolution:
    """Naive while-retry baseline: exit_code != 0 → RETRY (up to budget), else ACCEPT.

    No typed failure_mode, no intent, no capability check, no repair_hint.
    """
    exit_code = state.exec_state.exit_code
    retry_count = state.control_state.retry_count
    if exit_code is not None and exit_code != 0 and retry_count < config.max_retry:
        return RuntimeResolution(
            action="RETRY",
            outcome="",
            reason="naive: exit_code != 0",
            retryable=True,
        )
    if exit_code is None or exit_code == 0:
        return RuntimeResolution(
            action="ACCEPT",
            outcome="SUCCESS",
            reason="naive: exit_code == 0",
        )
    return RuntimeResolution(
        action="STOP",
        outcome="FAILED",
        reason="naive: budget exhausted",
    )


def retry_decision_node(state: RuntimeState) -> dict:
    if _is_bypass_enabled():
        resolution = _naive_resolution(state)
    else:
        governor = ExecutionGovernor(max_retries=config.max_retry)
        resolution = governor.resolve(state)

    # retry_decision_action and retry_count are set by wrap_retry_decision_node.
    control_state = state.control_state.model_copy(
        update={"policy_reason": resolution.reason}
    )
    semantic_state = state.semantic_state.model_copy(
        update={"task_intent": resolution.task_intent}
    )
    classification = {
        "intentional": resolution.intentional,
        "retryable": resolution.retryable,
        "failure_mode": resolution.failure_mode,
    }
    return {
        "classification_result": classification,
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
