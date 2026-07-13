"""OutcomeResolver — event-driven task outcome resolution.

Policy: RETRY / STOP / ACCEPT (what to do next)
Outcome: SUCCESS / RECOVERED / EXPECTED_FAILURE / FAILED / DENIED

Event-based architecture: RuntimeEvent → TaskOutcome mapping.
Intent-specific overrides handled as a separate layer.
"""

from __future__ import annotations

from enum import Enum

from reforge.runtime.domain.state.models import TIMEOUT_EXIT_CODE


class TaskOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    RECOVERED = "RECOVERED"
    EXPECTED_FAILURE = "EXPECTED_FAILURE"
    FAILED = "FAILED"
    DENIED = "DENIED"


class RuntimeEvent(str, Enum):
    """Deterministic runtime events — no reflection, no LLM, no semantic analysis."""
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EVALUATION_FAILED = "EVALUATION_FAILED"
    CLEAN_SUCCESS = "CLEAN_SUCCESS"
    RECOVERED_AFTER_RETRY = "RECOVERED_AFTER_RETRY"
    RETRIES_EXHAUSTED = "RETRIES_EXHAUSTED"
    REPEATED_SIGNATURE_STOP = "REPEATED_SIGNATURE_STOP"


# Default event → outcome mapping (before intent overrides)
_EVENT_OUTCOME_MAP: dict[RuntimeEvent, tuple[TaskOutcome, str]] = {
    RuntimeEvent.CAPABILITY_DENIED: (TaskOutcome.DENIED, "capability_policy"),
    RuntimeEvent.EXECUTION_TIMEOUT: (TaskOutcome.FAILED, "timeout"),
    RuntimeEvent.EXECUTION_FAILED: (TaskOutcome.FAILED, "execution_failed"),
    RuntimeEvent.EVALUATION_FAILED: (TaskOutcome.FAILED, "evaluation_failed"),
    RuntimeEvent.CLEAN_SUCCESS: (TaskOutcome.SUCCESS, "clean_execution"),
    RuntimeEvent.RECOVERED_AFTER_RETRY: (TaskOutcome.RECOVERED, "execution_recovered"),
    RuntimeEvent.RETRIES_EXHAUSTED: (TaskOutcome.FAILED, "retries_exhausted"),
    RuntimeEvent.REPEATED_SIGNATURE_STOP: (TaskOutcome.FAILED, "repeated_failure_signature"),
}

# Intent-based overrides — some intents reinterpret the default outcome
_INTENT_OUTCOME_OVERRIDES: dict[str, dict[RuntimeEvent, tuple[TaskOutcome, str]]] = {
    "STRESS_TEST": {
        RuntimeEvent.EXECUTION_TIMEOUT: (TaskOutcome.SUCCESS, "task_fidelity_achieved"),
    },
    "EXPECTED_ERROR": {
        RuntimeEvent.EXECUTION_FAILED: (TaskOutcome.EXPECTED_FAILURE, "task_fidelity_achieved"),
        RuntimeEvent.RETRIES_EXHAUSTED: (TaskOutcome.EXPECTED_FAILURE, "task_fidelity_achieved"),
    },
    "TRACEBACK_DEMO": {
        RuntimeEvent.EXECUTION_FAILED: (TaskOutcome.EXPECTED_FAILURE, "task_fidelity_achieved"),
        RuntimeEvent.RETRIES_EXHAUSTED: (TaskOutcome.EXPECTED_FAILURE, "task_fidelity_achieved"),
    },
    "RECOVERABLE_DEMO": {
        RuntimeEvent.CLEAN_SUCCESS: (TaskOutcome.RECOVERED, "execution_recovered"),
        RuntimeEvent.RECOVERED_AFTER_RETRY: (TaskOutcome.RECOVERED, "execution_recovered"),
    },
}


def _classify_event(
    execution_exit_code: int,
    retry_count: int,
    eval_passed: bool,
    policy_action: str,
    policy_reason: str = "",
) -> RuntimeEvent:
    """Map deterministic signals to a runtime event — no if-else chain on task_intent."""
    if execution_exit_code == TIMEOUT_EXIT_CODE:
        return RuntimeEvent.EXECUTION_TIMEOUT
    if policy_action == "STOP" and policy_reason == "repeated_failure_signature":
        # Deliberate early STOP — budget was NOT exhausted; reporting it as
        # RETRIES_EXHAUSTED would misstate why the run ended.
        return RuntimeEvent.REPEATED_SIGNATURE_STOP
    if policy_action == "STOP" and execution_exit_code != 0:
        return RuntimeEvent.RETRIES_EXHAUSTED
    if not eval_passed and policy_action == "STOP":
        return RuntimeEvent.EVALUATION_FAILED
    if execution_exit_code == 0 and retry_count > 0:
        return RuntimeEvent.RECOVERED_AFTER_RETRY
    if execution_exit_code == 0 and eval_passed:
        return RuntimeEvent.CLEAN_SUCCESS
    if execution_exit_code != 0:
        return RuntimeEvent.EXECUTION_FAILED
    return RuntimeEvent.EXECUTION_FAILED


def resolve_outcome(
    *,
    task_intent: str,
    execution_exit_code: int,
    retry_count: int,
    eval_passed: bool = True,
    policy_action: str = "",
    policy_reason: str = "",
) -> tuple[TaskOutcome, str]:
    """Resolve task outcome — event-driven with intent overrides.

    1. Classify event from deterministic signals
    2. Look up default outcome
    3. Apply intent-specific override if present
    """
    event = _classify_event(
        execution_exit_code, retry_count, eval_passed, policy_action, policy_reason
    )

    if task_intent in _INTENT_OUTCOME_OVERRIDES:
        overrides = _INTENT_OUTCOME_OVERRIDES[task_intent]
        if event in overrides:
            return overrides[event]

    return _EVENT_OUTCOME_MAP.get(event, (TaskOutcome.FAILED, "unknown"))
