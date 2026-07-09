"""Integration test for the self-healing retry loop."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import Mock, patch

from reforge.runtime.orchestration.engine.runner import RuntimeRunner

_INTENT_MOCK = Mock()
_INTENT_MOCK.chat = Mock(return_value="NORMAL_EXECUTION")


def _patch_llm_nodes(factory):
    """Patch LLMClient in every node module that instantiates it."""
    stack = ExitStack()
    for module in ("planner", "codegen", "reflection"):
        stack.enter_context(
            patch(f"reforge.runtime.orchestration.graph.nodes.{module}.LLMClient", factory)
        )
    return stack


def _make_llm_factory(responses: list[str]):
    """Return a factory that creates FakeLLM instances sharing a global response cursor."""

    cursor = [0]

    class _FakeLLM:
        def chat(self, _system: str, _user: str) -> str:
            idx = cursor[0]
            cursor[0] += 1
            if idx < len(responses):
                return responses[idx]
            return responses[-1]

    return _FakeLLM


class TestRetryLoop:
    def test_recovers_from_error(self):
        """Failing code -> reflection -> retry with fix -> success."""
        responses = [
            "1. Write and run Python code",          # planner
            "x = 1 / 0",                              # code_gen (initial)
            "ErrorType: ZeroDivisionError\nSummary: division by zero\nFix: replace with print(42)",  # reflection
            "print('result: 42')",                   # code_gen (retry)
        ]
        factory = _make_llm_factory(responses)

        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner()
            state = runner.run("test request")

        assert state.control_state.retry_count == 1, f"Expected 1 retry, got {state.control_state.retry_count}"  # 1 reflection → 1 retry
        assert state.execution_output is not None
        assert state.execution_output.exit_code == 0
        assert "42" in state.outcome_state.final_answer

    def test_success_first_try(self):
        """Successful execution on first attempt -> no retry."""
        responses = [
            "1. Write code",        # planner
            "print('result: 42')",  # code_gen
        ]
        factory = _make_llm_factory(responses)

        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner()
            state = runner.run("simple request")

        assert state.control_state.retry_count == 0, f"Expected 0 retries, got {state.control_state.retry_count}"
        assert state.execution_output is not None
        assert state.execution_output.exit_code == 0
        assert "42" in state.outcome_state.final_answer

    def test_retries_exhausted(self):
        """Code always fails -> retries exhausted -> final answer contains error."""
        responses = [
            "1. Execute code",                        # planner
            "x = 1 / 0",                              # code_gen (attempt 1)
            "ErrorType: ZeroDivisionError\nSummary: div by zero\nFix: remove",  # reflection 1
            "x = 1 / 0",                              # code_gen (attempt 2)
            "ErrorType: ZeroDivisionError\nSummary: div by zero\nFix: remove",  # reflection 2
            "x = 1 / 0",                              # code_gen (attempt 3)
            "ErrorType: ZeroDivisionError\nSummary: div by zero\nFix: remove",  # reflection 3
        ]
        factory = _make_llm_factory(responses)

        with (
            _patch_llm_nodes(factory),
            patch("reforge.runtime.policy.task_intent.LLMClient", return_value=_INTENT_MOCK),
        ):
            runner = RuntimeRunner()
            state = runner.run("always fail")

        assert state.control_state.retry_count == 3
        assert state.execution_output is not None
        assert state.execution_output.exit_code != 0
        assert "failed" in state.outcome_state.final_answer.lower()
