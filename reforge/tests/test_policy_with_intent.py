"""Integration test: PolicyEngine with TaskIntent — verifies STOP/RETRY/ACCEPT behavior."""

from __future__ import annotations

from unittest.mock import Mock, patch

from reforge.runtime.classification.classifier import FailureClassifier
from reforge.runtime.policy.retry_policy import RetryPolicy
from reforge.runtime.domain.state.models import (
    EvaluationResult,
    ExecutionOutput,
    RuntimeDecisionAction,
)

policy = RetryPolicy()
clf = FailureClassifier()


def _decide(request: str, exit_code: int, task_intent: str):
    execution = ExecutionOutput(exit_code=exit_code, stdout="ok")
    result = clf.classify(
        task_intent=task_intent,
        execution=execution,
        evaluation=None,
    )
    return policy.decide(
        classification=result.model_dump(),
        execution=execution,
        evaluation=None,
        retry_count=0,
        max_retries=2,
    )


def test_expected_error_stops():
    """EXPECTED_ERROR -> STOP, no retry."""
    decision = _decide("intentionally raise error", exit_code=1, task_intent="EXPECTED_ERROR")
    assert decision.action == RuntimeDecisionAction.STOP


def test_traceback_demo_stops():
    """TRACEBACK_DEMO -> STOP, no retry."""
    decision = _decide("traceback demo", exit_code=1, task_intent="TRACEBACK_DEMO")
    assert decision.action == RuntimeDecisionAction.STOP


def test_recoverable_demo_retries():
    """RECOVERABLE_DEMO -> RETRY."""
    decision = _decide("garbled char syntax error", exit_code=1, task_intent="RECOVERABLE_DEMO")
    assert decision.action == RuntimeDecisionAction.RETRY


def test_stress_test_stops():
    """STRESS_TEST -> STOP, no retry."""
    decision = _decide("while True loop", exit_code=-1, task_intent="STRESS_TEST")
    assert decision.action == RuntimeDecisionAction.STOP


def test_normal_execution_retries_on_error():
    """NORMAL_EXECUTION with error -> RETRY."""
    decision = _decide("csv revenue average", exit_code=1, task_intent="NORMAL_EXECUTION")
    assert decision.action == RuntimeDecisionAction.RETRY


def test_normal_execution_accepts_on_success():
    """NORMAL_EXECUTION + clean -> ACCEPT."""
    decision = _decide("csv revenue average", exit_code=0, task_intent="NORMAL_EXECUTION")
    assert decision.action == RuntimeDecisionAction.ACCEPT
