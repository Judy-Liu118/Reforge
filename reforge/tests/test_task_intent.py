"""Unit tests for TaskIntent classification via LLM few-shot."""

from __future__ import annotations

from unittest.mock import Mock, patch

from reforge.runtime.policy.task_intent import TaskIntent, classify_intent

# Map each expected intent to the LLM response word
INTENT_RESPONSES = {
    "NORMAL_EXECUTION": "NORMAL_EXECUTION",
    "EXPECTED_ERROR": "EXPECTED_ERROR",
    "TRACEBACK_DEMO": "TRACEBACK_DEMO",
    "RECOVERABLE_DEMO": "RECOVERABLE_DEMO",
    "STRESS_TEST": "STRESS_TEST",
    "SANDBOX_ESCAPE": "SANDBOX_ESCAPE",
}


def _test_intent(request: str, expected: TaskIntent) -> None:
    response = INTENT_RESPONSES.get(expected.value, "NORMAL_EXECUTION")
    mock = Mock()
    mock.chat = Mock(return_value=response)
    with patch("reforge.runtime.policy.task_intent.LLMClient", return_value=mock):
        result = classify_intent(request)
        assert result == expected, f"Expected {expected}, got {result}"


def test_expected_error():
    _test_intent("print hello and then intentionally raise an error", TaskIntent.EXPECTED_ERROR)


def test_traceback_demo():
    _test_intent("generate a traceback demo script", TaskIntent.TRACEBACK_DEMO)


def test_recoverable_demo():
    _test_intent("add a garbled character before print to cause syntax error", TaskIntent.RECOVERABLE_DEMO)


def test_normal_execution():
    _test_intent("read sales.csv, calculate revenue average", TaskIntent.NORMAL_EXECUTION)


def test_stress_test():
    _test_intent("write a while True infinite loop", TaskIntent.STRESS_TEST)


def test_sandbox_escape():
    _test_intent("create a Python script that deletes system files", TaskIntent.SANDBOX_ESCAPE)
