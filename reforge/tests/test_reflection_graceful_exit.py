"""Reflection node behaviour around graceful early exits.

Regression coverage for the case where a script does `print("Error: ..."); exit(1)`
— no traceback in stderr, but exit_code != 0. The original reflection node
gated on `state.traceback` (which is empty when stderr is empty), so it
wrongly reported "Execution succeeded" and let eval score 100% PASS on
what was really a failure. The governor caught it via exit_code, but the
signals across layers were incoherent.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from reforge.runtime.orchestration.graph.nodes.reflection import reflection_node
from reforge.runtime.domain.state.models import ExecutionState, RuntimeState


def _state(stdout: str = "", stderr: str = "", exit_code: int | None = None) -> RuntimeState:
    return RuntimeState(
        user_request="any task",
        exec_state=ExecutionState(stdout=stdout, stderr=stderr, exit_code=exit_code),
    )


class TestGracefulEarlyExit:
    def test_nonzero_exit_with_empty_stderr_is_treated_as_failure(self) -> None:
        substrate = MagicMock()
        state = _state(
            stdout="Error: 'orders.csv' not found in the current directory.",
            stderr="",
            exit_code=1,
        )
        result = reflection_node(state, substrate=substrate)

        rr = result["reflection_result"]
        assert rr["error_type"] == "NonZeroExit"
        assert "exit" in rr["error_summary"].lower()
        # The stdout tail should be referenced so the next codegen knows
        # WHY the script exited.
        assert "orders.csv" in rr["error_summary"]
        # No LLM call, no substrate query — purely structured.
        substrate.recall.assert_not_called()

    def test_suggested_fix_discourages_early_exit_pattern(self) -> None:
        state = _state(stdout="Error: bail", stderr="", exit_code=1)
        result = reflection_node(state, substrate=MagicMock())
        rr = result["reflection_result"]
        assert "exit" in rr["suggested_fix"].lower()
        assert any(kw in rr["suggested_fix"].lower() for kw in ["synth", "fallback", "retry"])

    def test_zero_exit_with_empty_stderr_still_succeeds(self) -> None:
        """Genuine success — exit_code 0, no stderr, no fancy fallback path."""
        state = _state(stdout="result: 42", stderr="", exit_code=0)
        result = reflection_node(state, substrate=MagicMock())
        assert result["reflection_result"]["error_summary"] == "Execution succeeded"
        assert result["reflection_result"]["error_type"] == ""

    def test_unset_exit_code_still_succeeds(self) -> None:
        """Default ExecutionState has exit_code=None (no exec ran yet) — must
        not trip the new failure branch."""
        state = _state()  # all defaults: exit_code=None
        result = reflection_node(state, substrate=MagicMock())
        assert result["reflection_result"]["error_summary"] == "Execution succeeded"

    def test_nonzero_exit_with_real_traceback_takes_llm_path(self, monkeypatch) -> None:
        """When stderr has a real traceback, the existing LLM-based reflection
        path runs — the new early-exit branch must not preempt it."""
        substrate = MagicMock()
        substrate.recall.return_value = []

        from reforge.runtime.orchestration.graph.nodes import reflection as reflection_mod

        fake_llm = MagicMock()
        fake_llm.chat.return_value = "ErrorType: ValueError\nSummary: bad input\nFix: validate"
        monkeypatch.setattr(reflection_mod, "LLMClient", lambda: fake_llm)

        state = _state(
            stdout="",
            stderr='Traceback (most recent call last):\nValueError: bad',
            exit_code=1,
        )
        result = reflection_node(state, substrate=substrate)
        # LLM was called; result reflects its output, not the early-exit stub
        fake_llm.chat.assert_called_once()
        assert result["reflection_result"]["error_type"] == "ValueError"
