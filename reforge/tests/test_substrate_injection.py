"""Contract tests for P-R.4 — MemorySubstrate is injected, not hard-wired.

A mock substrate replaces the default; both the planner and reflection nodes
should query it instead of constructing CompositeMemorySubstrate themselves.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from reforge.memory.models import MemoryRecord, MemoryType
from reforge.runtime.orchestration.graph.nodes.planner import planner_node
from reforge.runtime.orchestration.graph.nodes.reflection import reflection_node
from reforge.runtime.domain.state.models import ExecutionState, RuntimeState


def _record(error_type: str = "KeyError") -> MemoryRecord:
    return MemoryRecord(
        memory_type=MemoryType.RECOVERY,
        user_request="prior task",
        error_type=error_type,
        recovery_action="apply known fix",
        outcome="success",
    )


class TestPlannerSubstrateInjection:
    def test_planner_node_queries_injected_substrate(self, monkeypatch) -> None:
        """planner_node should call substrate.recall_for_planning when provided."""
        substrate = MagicMock()
        substrate.recall_for_planning.return_value = [_record()]

        # Stub LLM so the test does not require a real backend
        from reforge.runtime.orchestration.graph.nodes import planner as planner_mod

        fake_llm = MagicMock()
        fake_llm.chat.return_value = "1. Plan"
        monkeypatch.setattr(planner_mod, "LLMClient", lambda: fake_llm)

        state = RuntimeState(user_request="test")
        result = planner_node(state, substrate=substrate)

        substrate.recall_for_planning.assert_called_once()
        assert result["generated_code"] == "1. Plan"


class TestReflectionSubstrateInjection:
    def test_reflection_node_queries_injected_substrate(self, monkeypatch) -> None:
        substrate = MagicMock()
        substrate.recall.return_value = [_record()]

        from reforge.runtime.orchestration.graph.nodes import reflection as reflection_mod

        fake_llm = MagicMock()
        fake_llm.chat.return_value = (
            "ErrorType: KeyError\nSummary: missing\nFix: use .get"
        )
        monkeypatch.setattr(reflection_mod, "LLMClient", lambda: fake_llm)

        state = RuntimeState(
            user_request="run x",
            exec_state=ExecutionState(
                stderr='Traceback (most recent call last):\n  ...\nKeyError: "col"',
                exit_code=1,
            ),
        )
        result = reflection_node(state, substrate=substrate)

        substrate.recall.assert_called_once()
        assert result["reflection_result"]["error_type"] == "KeyError"

    def test_reflection_node_skips_substrate_on_no_traceback(self, monkeypatch) -> None:
        """No traceback = clean execution = no need to query past failures."""
        substrate = MagicMock()
        state = RuntimeState(user_request="x")
        result = reflection_node(state, substrate=substrate)

        substrate.recall.assert_not_called()
        assert result["reflection_result"]["error_summary"] == "Execution succeeded"


class TestRunnerForwardsSubstrate:
    def test_runtime_runner_accepts_memory_substrate_kwarg(self) -> None:
        from reforge.memory.substrate import CompositeMemorySubstrate
        from reforge.runtime.orchestration.engine.runner import RuntimeRunner

        substrate = CompositeMemorySubstrate()
        runner = RuntimeRunner(memory_substrate=substrate)
        assert runner is not None
