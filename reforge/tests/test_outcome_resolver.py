"""Unit tests for OutcomeResolver — verifies all 5 outcome scenarios."""

from reforge.runtime.orchestration.outcome_resolver import TaskOutcome, resolve_outcome


def test_expected_error():
    outcome, reason = resolve_outcome(
        task_intent="EXPECTED_ERROR", execution_exit_code=1, retry_count=0,
        policy_action="STOP",
    )
    assert outcome == TaskOutcome.EXPECTED_FAILURE


def test_traceback_demo():
    outcome, reason = resolve_outcome(
        task_intent="TRACEBACK_DEMO", execution_exit_code=1, retry_count=0,
        policy_action="STOP",
    )
    assert outcome == TaskOutcome.EXPECTED_FAILURE


def test_stress_test_success():
    outcome, reason = resolve_outcome(
        task_intent="STRESS_TEST", execution_exit_code=-1, retry_count=0,
        policy_action="STOP",
    )
    assert outcome == TaskOutcome.SUCCESS
    assert reason == "task_fidelity_achieved"


def test_recoverable_demo():
    outcome, reason = resolve_outcome(
        task_intent="RECOVERABLE_DEMO", execution_exit_code=0, retry_count=1,
        policy_action="ACCEPT",
    )
    assert outcome == TaskOutcome.RECOVERED


def test_normal_timeout_failed():
    outcome, reason = resolve_outcome(
        task_intent="NORMAL_EXECUTION", execution_exit_code=-1, retry_count=0,
        policy_action="STOP",
    )
    assert outcome == TaskOutcome.FAILED


def test_normal_success():
    outcome, reason = resolve_outcome(
        task_intent="NORMAL_EXECUTION", execution_exit_code=0, retry_count=0,
    )
    assert outcome == TaskOutcome.SUCCESS


def test_retries_exhausted():
    outcome, reason = resolve_outcome(
        task_intent="NORMAL_EXECUTION", execution_exit_code=1, retry_count=3,
        policy_action="STOP",
    )
    assert outcome == TaskOutcome.FAILED


def test_normal_recovered():
    outcome, reason = resolve_outcome(
        task_intent="NORMAL_EXECUTION", execution_exit_code=0, retry_count=1,
    )
    assert outcome == TaskOutcome.RECOVERED
