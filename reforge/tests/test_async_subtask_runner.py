"""Tests for AsyncSubtaskRunner — parallel execution and dependency propagation."""

from __future__ import annotations

from unittest.mock import MagicMock


from reforge.runtime.orchestration.decomposition.async_runner import AsyncSubtaskRunner, _group_by_levels
from reforge.runtime.orchestration.decomposition.models import (
    DecompositionResult,
    SubtaskPlan,
    SubtaskRuntimeState,
)


def _plan(index: int, depends_on: list[int] | None = None, request: str = "") -> SubtaskPlan:
    return SubtaskPlan(
        index=index,
        request=request or f"task {index}",
        description=f"step {index}",
        depends_on=depends_on or [],
    )


def _decomp(subtasks: list[SubtaskPlan]) -> DecompositionResult:
    return DecompositionResult(
        is_multistep=len(subtasks) > 1,
        subtasks=subtasks,
        original_request="original",
    )


def _mock_run_one(subtask: SubtaskPlan) -> SubtaskRuntimeState:
    """Fake run_one that returns a SubtaskRuntimeState with SUCCESS immediately."""
    mock_state = MagicMock()
    mock_state.outcome_state.task_outcome = "SUCCESS"
    mock_state.outcome_state.final_answer = f"answer for {subtask.request}"
    mock_state.control_state.retry_count = 0
    return SubtaskRuntimeState(
        subtask=subtask,
        session_id="mock-session",
        state=mock_state,
        duration_ms=0.0,
    )


# --- Independent subtasks (no deps) ---

def test_independent_subtasks_all_complete() -> None:
    subtasks = [_plan(0), _plan(1), _plan(2)]
    decomp = _decomp(subtasks)

    runner = AsyncSubtaskRunner()
    runner._sync_runner.run_one =_mock_run_one
    result = runner.run_all(decomp)

    assert len(result.subtask_results) == 3
    assert result.overall_outcome == "COMPLETE"


def test_sequential_chain_runs_in_order() -> None:
    """Results from earlier steps appear in later steps' requests."""
    subtasks = [_plan(0, request="step 0 task"), _plan(1, depends_on=[0], request="step 1 task")]
    decomp = _decomp(subtasks)
    captured_requests: list[str] = []

    def capturing_run_one(subtask: SubtaskPlan) -> SubtaskRuntimeState:
        captured_requests.append(subtask.request)
        return _mock_run_one(subtask)

    runner = AsyncSubtaskRunner()
    runner._sync_runner.run_one =capturing_run_one
    result = runner.run_all(decomp)

    assert len(result.subtask_results) == 2
    # Step 1's request should contain step 0's result (context injection)
    assert "answer for step 0 task" in captured_requests[1]


def test_parallel_level_produces_all_results() -> None:
    """All subtasks in a parallel level complete even with concurrency."""
    subtasks = [_plan(0), _plan(1), _plan(2)]
    decomp = _decomp(subtasks)

    runner = AsyncSubtaskRunner(max_workers=3)
    runner._sync_runner.run_one =_mock_run_one
    result = runner.run_all(decomp)

    assert len(result.subtask_results) == 3
    assert all(sr.task_outcome == "SUCCESS" for sr in result.subtask_results)


def test_failed_subtask_does_not_abort_others() -> None:
    """A failing subtask in a parallel level should not prevent others from running."""
    call_count = 0

    def partial_fail(subtask: SubtaskPlan) -> SubtaskRuntimeState:
        nonlocal call_count
        call_count += 1
        if subtask.index == 1:
            raise RuntimeError("deliberate failure")
        return _mock_run_one(subtask)

    subtasks = [_plan(0), _plan(1), _plan(2)]
    decomp = _decomp(subtasks)

    runner = AsyncSubtaskRunner(max_workers=3)
    runner._sync_runner.run_one =partial_fail
    result = runner.run_all(decomp)

    # All three were attempted; failed one has FAILED outcome
    assert call_count == 3
    failed = [sr for sr in result.subtask_results if sr.task_outcome == "FAILED"]
    assert len(failed) == 1


def test_failed_subtask_captures_error_in_final_answer() -> None:
    """When a parallel worker raises, the error must surface — not be silently swallowed."""

    def fail_one(subtask: SubtaskPlan) -> SubtaskRuntimeState:
        if subtask.index == 1:
            raise RuntimeError("disk full")
        return _mock_run_one(subtask)

    subtasks = [_plan(0), _plan(1), _plan(2)]
    decomp = _decomp(subtasks)

    runner = AsyncSubtaskRunner(max_workers=3)
    runner._sync_runner.run_one = fail_one
    result = runner.run_all(decomp)

    failed = [sr for sr in result.subtask_results if sr.task_outcome == "FAILED"]
    assert len(failed) == 1
    # SubtaskResult.final_answer carries the SubtaskRuntimeState.error string
    assert "RuntimeError" in failed[0].final_answer
    assert "disk full" in failed[0].final_answer


def test_failed_subtask_logs_exception(caplog) -> None:
    """logger.exception must fire so the failure is observable in the operator log."""
    import logging

    def fail_one(subtask: SubtaskPlan) -> SubtaskRuntimeState:
        if subtask.index == 1:
            raise RuntimeError("kaboom")
        return _mock_run_one(subtask)

    subtasks = [_plan(0), _plan(1), _plan(2)]
    decomp = _decomp(subtasks)

    runner = AsyncSubtaskRunner(max_workers=3)
    runner._sync_runner.run_one = fail_one
    with caplog.at_level(
        logging.ERROR,
        logger="reforge.runtime.orchestration.decomposition.async_runner",
    ):
        runner.run_all(decomp)

    assert any(
        "raised in AsyncSubtaskRunner" in rec.message for rec in caplog.records
    ), f"expected exception log; got: {[r.message for r in caplog.records]}"


def test_subtask_runtime_state_error_round_trip() -> None:
    """SubtaskRuntimeState.to_result must surface .error into SubtaskResult.final_answer."""
    srs = SubtaskRuntimeState(
        subtask=_plan(0, request="anything"),
        session_id="",
        state=None,
        duration_ms=0.0,
        error="ValueError: bad input",
    )
    result = srs.to_result()
    assert result.task_outcome == "FAILED"
    assert result.final_answer == "ValueError: bad input"


# --- _group_by_levels parallel detection ---

def test_parallel_levels_detected() -> None:
    subtasks = [_plan(0), _plan(1), _plan(2, depends_on=[0, 1])]
    levels = _group_by_levels(subtasks)
    parallel_count = sum(1 for lv in levels if len(lv) > 1)
    assert parallel_count >= 1


def test_no_parallel_in_linear_chain() -> None:
    subtasks = [_plan(0), _plan(1, [0]), _plan(2, [1])]
    levels = _group_by_levels(subtasks)
    assert all(len(lv) == 1 for lv in levels)
