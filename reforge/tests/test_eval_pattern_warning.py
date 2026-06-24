"""Tests for P12.4 find_by_eval_pattern + P12.5 ClassifyStage evaluation learning."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reforge.runtime.domain.state.models import EvalCheck, EvaluationResult, ExecutionState
from reforge.runtime.infrastructure.trajectory.models import AttemptStep, TrajectoryRecord
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore


# --- P12.4: find_by_eval_pattern ---

def _traj_with_eval(tmp_path: Path, eval_failure_type: str, session_id: str = "s1") -> TrajectoryRecord:
    step = AttemptStep(
        attempt=0,
        exit_code=1,
        error_type="KeyError",
        eval_score=0.3,
        eval_failure_type=eval_failure_type,
    )
    rec = TrajectoryRecord(
        trajectory_id="t1",
        session_id=session_id,
        timestamp="2026-01-01T00:00:00Z",
        user_request="analyze csv",
        task_intent="NORMAL_EXECUTION",
        total_attempts=1,
        final_outcome="FAILED",
        steps=[step],
    )
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    store.save(rec)
    return rec


def test_find_by_eval_pattern_returns_matching(tmp_path: Path) -> None:
    _traj_with_eval(tmp_path, "blanket_except_detected", "s1")
    _traj_with_eval(tmp_path, "blanket_except_detected", "s2")
    _traj_with_eval(tmp_path, "empty_output", "s3")

    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    results = store.find_by_eval_pattern("blanket_except_detected")

    assert len(results) == 2
    for r in results:
        assert any(s.eval_failure_type == "blanket_except_detected" for s in r.steps)


def test_find_by_eval_pattern_empty_when_no_match(tmp_path: Path) -> None:
    _traj_with_eval(tmp_path, "output_not_empty", "s1")
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    results = store.find_by_eval_pattern("blanket_except_detected")
    assert results == []


def test_find_by_eval_pattern_empty_failure_type_returns_empty(tmp_path: Path) -> None:
    _traj_with_eval(tmp_path, "some_type")
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    assert store.find_by_eval_pattern("") == []


def test_find_by_eval_pattern_respects_limit(tmp_path: Path) -> None:
    for i in range(5):
        _traj_with_eval(tmp_path, "retry_drift", f"s{i}")
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    results = store.find_by_eval_pattern("retry_drift", limit=3)
    assert len(results) == 3


def test_find_by_eval_pattern_empty_store(tmp_path: Path) -> None:
    store = TrajectoryStore(path=tmp_path / "empty.jsonl")
    assert store.find_by_eval_pattern("any_type") == []


# --- count_by_eval_pattern: honest recurrence count, no limit truncation ---

def test_count_by_eval_pattern_returns_full_count(tmp_path: Path) -> None:
    # 7 matching trajectories — must not be truncated like find_by_eval_pattern's limit.
    for i in range(7):
        _traj_with_eval(tmp_path, "blanket_except_detected", f"s{i}")
    _traj_with_eval(tmp_path, "empty_output", "s99")
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    assert store.count_by_eval_pattern("blanket_except_detected") == 7


def test_count_by_eval_pattern_zero_for_unseen(tmp_path: Path) -> None:
    _traj_with_eval(tmp_path, "output_not_empty", "s1")
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    assert store.count_by_eval_pattern("blanket_except_detected") == 0


def test_count_by_eval_pattern_empty_failure_type_returns_zero(tmp_path: Path) -> None:
    _traj_with_eval(tmp_path, "some_type")
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    assert store.count_by_eval_pattern("") == 0


def test_count_by_eval_pattern_empty_store(tmp_path: Path) -> None:
    store = TrajectoryStore(path=tmp_path / "empty.jsonl")
    assert store.count_by_eval_pattern("any_type") == 0


# --- P12.5: ClassifyStage evaluation pattern learning ---

def _make_eval_result(failure_type: str, passed: bool = False) -> EvaluationResult:
    check = EvalCheck(name=failure_type, passed=False, detail="failed")
    return EvaluationResult(
        passed=passed,
        score=0.3,
        checks=[check],
        summary="test",
        failure_type=failure_type,
    )


def test_classify_stage_injects_pattern_warning_when_threshold_met(tmp_path: Path) -> None:
    from reforge.runtime.orchestration.governor.classify_stage import ClassifyStage
    from reforge.runtime.orchestration.governor.stages import RuntimeContext
    from reforge.runtime.domain.state.models import RuntimeState

    # Pre-load 2 trajectories with the same eval failure type
    traj_store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    for i in range(2):
        _traj_with_eval(tmp_path, "blanket_except_detected", f"hist-{i}")

    state = RuntimeState(
        user_request="analyze csv",
        exec_state=ExecutionState(stdout="", stderr="error", exit_code=1),
    )
    state = state.model_copy(update={"semantic_state": state.semantic_state.model_copy(
        update={"evaluation_result": _make_eval_result("blanket_except_detected")}
    )})

    ctx = RuntimeContext(state=state, request="analyze csv", task_intent="NORMAL_EXECUTION")
    ctx.classification.retryable = True
    ctx.classification.intentional = False
    ctx.classification.failure_mode = "execution_error"

    stage = ClassifyStage(trajectory_store=traj_store)
    # Patch classifier to avoid LLM calls
    stage._classifier.classify = MagicMock(return_value=MagicMock(
        intentional=False, retryable=True, failure_mode="execution_error",
    ))
    ctx = stage.execute(ctx)

    assert "blanket_except_detected" in ctx.repair_hint
    assert "recurring" in ctx.repair_hint


def test_classify_stage_no_warning_below_threshold(tmp_path: Path) -> None:
    from reforge.runtime.orchestration.governor.classify_stage import ClassifyStage
    from reforge.runtime.orchestration.governor.stages import RuntimeContext
    from reforge.runtime.domain.state.models import RuntimeState

    # Only 1 trajectory — below threshold of 2
    traj_store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    _traj_with_eval(tmp_path, "blanket_except_detected", "hist-0")

    state = RuntimeState(
        user_request="analyze csv",
        exec_state=ExecutionState(stdout="", stderr="error", exit_code=1),
    )
    state = state.model_copy(update={"semantic_state": state.semantic_state.model_copy(
        update={"evaluation_result": _make_eval_result("blanket_except_detected")}
    )})

    ctx = RuntimeContext(state=state, request="analyze csv", task_intent="NORMAL_EXECUTION")
    ctx.classification.retryable = True
    ctx.classification.failure_mode = "execution_error"

    stage = ClassifyStage(trajectory_store=traj_store)
    stage._classifier.classify = MagicMock(return_value=MagicMock(
        intentional=False, retryable=True, failure_mode="execution_error",
    ))
    ctx = stage.execute(ctx)

    assert "recurring" not in (ctx.repair_hint or "")


def test_classify_stage_no_warning_when_eval_passed(tmp_path: Path) -> None:
    from reforge.runtime.orchestration.governor.classify_stage import ClassifyStage
    from reforge.runtime.orchestration.governor.stages import RuntimeContext
    from reforge.runtime.domain.state.models import RuntimeState

    traj_store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    for i in range(3):
        _traj_with_eval(tmp_path, "blanket_except_detected", f"h{i}")

    from reforge.runtime.domain.state.models import SemanticState
    state = RuntimeState(
        user_request="task",
        semantic_state=SemanticState(evaluation_result=EvaluationResult(
            passed=True, score=1.0, checks=[], summary="ok", failure_type=""
        )),
    )
    ctx = RuntimeContext(state=state, request="task", task_intent="NORMAL_EXECUTION")
    ctx.classification.retryable = True
    ctx.classification.failure_mode = "execution_error"

    stage = ClassifyStage(trajectory_store=traj_store)
    stage._classifier.classify = MagicMock(return_value=MagicMock(
        intentional=False, retryable=True, failure_mode="execution_error",
    ))
    ctx = stage.execute(ctx)

    assert "recurring" not in (ctx.repair_hint or "")
