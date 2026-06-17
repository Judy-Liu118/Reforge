"""Regression tests for ExecutionGovernor — unified runtime authority."""

from unittest.mock import Mock, patch

from reforge.runtime.orchestration.governor import ExecutionGovernor, RuntimeResolution
from reforge.runtime.policy.task_intent import TaskIntent
from reforge.runtime.domain.state.models import (
    EvaluationResult, ExecutionState, RuntimeControlState, RuntimeState,
)

_INTENT_MOCK = Mock(return_value=TaskIntent.NORMAL_EXECUTION)
_INTENT_EXPECTED = Mock(return_value=TaskIntent.EXPECTED_ERROR)
_INTENT_STRESS = Mock(return_value=TaskIntent.STRESS_TEST)
_INTENT_RECOVERABLE = Mock(return_value=TaskIntent.RECOVERABLE_DEMO)


def _state(exit_code: int = 0, task_intent: str = "NORMAL_EXECUTION",
           retry_count: int = 0, user_request: str = "test") -> RuntimeState:
    return RuntimeState(
        user_request=user_request,
        exec_state=ExecutionState(exit_code=exit_code, stdout="ok"),
        control_state=RuntimeControlState(retry_count=retry_count),
        evaluation_result=EvaluationResult(passed=True),
        classification_result={"intentional": False, "retryable": False, "failure_mode": "none"},
    )


def _resolve(state, intent_mock=_INTENT_MOCK):
    gov = ExecutionGovernor()
    with patch("reforge.runtime.orchestration.governor.intent_stage.classify_intent", intent_mock):
        return gov.resolve(state)


def test_normal_success():
    r = _resolve(_state(exit_code=0))
    assert r.action == "ACCEPT"
    assert r.outcome == "SUCCESS"


def test_normal_error_retry():
    r = _resolve(_state(exit_code=1, retry_count=0))
    assert r.action == "RETRY"


def test_expected_error_stops():
    s = _state(exit_code=1)
    r = _resolve(s, intent_mock=_INTENT_EXPECTED)
    assert r.action == "STOP"
    assert r.outcome == "EXPECTED_FAILURE"


def test_stress_test_success():
    s = _state(exit_code=-1)
    r = _resolve(s, intent_mock=_INTENT_STRESS)
    assert r.outcome == "SUCCESS"


def test_capability_deny():
    s = _state(user_request="run rm -rf / to delete everything")
    r = _resolve(s)
    assert r.action == "DENY"
    assert r.outcome == "DENIED"


def test_recovered():
    s = _state(exit_code=0, retry_count=1)
    r = _resolve(s, intent_mock=_INTENT_RECOVERABLE)
    assert r.outcome == "RECOVERED"
