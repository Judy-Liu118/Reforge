"""Tests for SubtaskRunner and MultiStepResult aggregation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from reforge.runtime.orchestration.decomposition.models import (
    DecompositionResult,
    MultiStepResult,
    SubtaskPlan,
    SubtaskResult,
)
from reforge.runtime.orchestration.decomposition.runner import SubtaskRunner


def _make_decomposition(requests: list[str]) -> DecompositionResult:
    subtasks = [SubtaskPlan(index=i, request=r, description=f"step {i}") for i, r in enumerate(requests)]
    return DecompositionResult(
        is_multistep=len(requests) > 1,
        subtasks=subtasks,
        original_request=" / ".join(requests),
    )


def _mock_runner_for(outcome: str, final_answer: str, retry_count: int = 0) -> MagicMock:
    """Build a RuntimeRunner mock that yields one (node_name, state) pair."""
    state = MagicMock()
    state.outcome_state.task_outcome = outcome
    state.outcome_state.final_answer = final_answer
    state.control_state.retry_count = retry_count
    state.attempts = []
    state.execution_output = None

    runner_mock = MagicMock()
    runner_mock.stream.return_value = iter([("final_response", state)])
    runner_mock.session_id = "mock-session"
    return runner_mock


# --- MultiStepResult aggregation ---

def test_multistep_result_complete_when_all_succeed() -> None:
    results = [
        SubtaskResult(subtask=SubtaskPlan(index=0, request="A"), task_outcome="SUCCESS", final_answer="ans A"),
        SubtaskResult(subtask=SubtaskPlan(index=1, request="B"), task_outcome="RECOVERED", final_answer="ans B"),
    ]
    result = MultiStepResult.from_results("original", results)
    assert result.overall_outcome == "COMPLETE"
    assert "Step 1" in result.final_answer
    assert "Step 2" in result.final_answer


def test_multistep_result_partial_when_some_fail() -> None:
    results = [
        SubtaskResult(subtask=SubtaskPlan(index=0, request="A"), task_outcome="SUCCESS", final_answer="ok"),
        SubtaskResult(subtask=SubtaskPlan(index=1, request="B"), task_outcome="FAILED", final_answer=""),
    ]
    result = MultiStepResult.from_results("original", results)
    assert result.overall_outcome == "PARTIAL"


def test_multistep_result_failed_when_all_fail() -> None:
    results = [
        SubtaskResult(subtask=SubtaskPlan(index=0, request="A"), task_outcome="FAILED", final_answer=""),
        SubtaskResult(subtask=SubtaskPlan(index=1, request="B"), task_outcome="FAILED", final_answer=""),
    ]
    result = MultiStepResult.from_results("original", results)
    assert result.overall_outcome == "FAILED"


# --- SubtaskRunner.run_all ---

def test_subtask_runner_runs_each_subtask(tmp_path) -> None:
    decomposition = _make_decomposition(["task A", "task B"])

    mock_runners = [
        _mock_runner_for("SUCCESS", "result A"),
        _mock_runner_for("SUCCESS", "result B"),
    ]

    with patch("reforge.runtime.orchestration.decomposition.runner.RuntimeRunner", side_effect=mock_runners):
        runner = SubtaskRunner()
        result = runner.run_all(decomposition)

    assert len(result.subtask_results) == 2
    assert result.overall_outcome == "COMPLETE"


def test_subtask_runner_handles_failed_subtask(tmp_path) -> None:
    decomposition = _make_decomposition(["task A", "task B"])

    mock_runners = [
        _mock_runner_for("SUCCESS", "ok"),
        _mock_runner_for("FAILED", ""),
    ]

    with patch("reforge.runtime.orchestration.decomposition.runner.RuntimeRunner", side_effect=mock_runners):
        runner = SubtaskRunner()
        result = runner.run_all(decomposition)

    assert result.overall_outcome == "PARTIAL"


# --- SubtaskRunner.stream_all ---

def test_subtask_runner_stream_yields_subtask_index(tmp_path) -> None:
    decomposition = _make_decomposition(["task A", "task B"])

    mock_runners = [
        _mock_runner_for("SUCCESS", "A"),
        _mock_runner_for("RECOVERED", "B"),
    ]

    with patch("reforge.runtime.orchestration.decomposition.runner.RuntimeRunner", side_effect=mock_runners):
        runner = SubtaskRunner()
        events = list(runner.stream_all(decomposition))

    # Each subtask yields at least one (index, node_name, state)
    indices = [idx for idx, _, _ in events]
    assert 0 in indices
    assert 1 in indices


# --- DecompositionResult.single ---

def test_single_task_has_one_subtask() -> None:
    result = DecompositionResult.single("analyze csv")
    assert not result.is_multistep
    assert len(result.subtasks) == 1
    assert result.subtasks[0].request == "analyze csv"
