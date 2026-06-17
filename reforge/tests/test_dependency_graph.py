"""Tests for _group_by_levels topological sort and _enrich_subtask context injection."""

from __future__ import annotations

import pytest

from reforge.runtime.orchestration.decomposition.async_runner import _enrich_subtask, _group_by_levels
from reforge.runtime.orchestration.decomposition.models import SubtaskPlan, SubtaskResult


def _plan(index: int, depends_on: list[int] | None = None) -> SubtaskPlan:
    return SubtaskPlan(
        index=index,
        request=f"task {index}",
        description=f"step {index}",
        depends_on=depends_on or [],
    )


# --- _group_by_levels ---

def test_all_independent_is_one_level() -> None:
    subtasks = [_plan(0), _plan(1), _plan(2)]
    levels = _group_by_levels(subtasks)
    assert len(levels) == 1
    assert len(levels[0]) == 3


def test_linear_chain_is_sequential() -> None:
    subtasks = [_plan(0), _plan(1, [0]), _plan(2, [1])]
    levels = _group_by_levels(subtasks)
    assert len(levels) == 3
    assert levels[0][0].index == 0
    assert levels[1][0].index == 1
    assert levels[2][0].index == 2


def test_diamond_dependency() -> None:
    # 0 → {1, 2} → 3
    subtasks = [_plan(0), _plan(1, [0]), _plan(2, [0]), _plan(3, [1, 2])]
    levels = _group_by_levels(subtasks)
    assert len(levels) == 3
    assert levels[0][0].index == 0
    assert {s.index for s in levels[1]} == {1, 2}  # parallel level
    assert levels[2][0].index == 3


def test_single_subtask_is_one_level() -> None:
    levels = _group_by_levels([_plan(0)])
    assert len(levels) == 1
    assert len(levels[0]) == 1


def test_cycle_does_not_hang() -> None:
    """Cyclic deps must not loop forever — fallback to sequential."""
    subtasks = [_plan(0, [1]), _plan(1, [0])]
    levels = _group_by_levels(subtasks)
    # Should not raise; levels may not be ideal but execution terminates
    total = sum(len(lv) for lv in levels)
    assert total == 2


def test_mixed_deps_correct_levels() -> None:
    # 0, 1 independent; 2 depends on 0; 3 depends on 1 and 2
    subtasks = [_plan(0), _plan(1), _plan(2, [0]), _plan(3, [1, 2])]
    levels = _group_by_levels(subtasks)
    # Level 0: {0, 1} | Level 1: {2} | Level 2: {3}
    assert len(levels) == 3
    assert {s.index for s in levels[0]} == {0, 1}
    assert levels[1][0].index == 2
    assert levels[2][0].index == 3


# --- _enrich_subtask ---

def _result(index: int, final_answer: str) -> SubtaskResult:
    return SubtaskResult(
        subtask=_plan(index),
        task_outcome="SUCCESS",
        final_answer=final_answer,
        session_id="s",
    )


def test_no_deps_returns_original() -> None:
    subtask = _plan(0)
    enriched = _enrich_subtask(subtask, {})
    assert enriched.request == subtask.request


def test_deps_injects_context() -> None:
    subtask = _plan(1, depends_on=[0])
    completed = {0: _result(0, "answer from step 0")}
    enriched = _enrich_subtask(subtask, completed)
    assert "answer from step 0" in enriched.request
    assert "[Step 1 result]" in enriched.request


def test_deps_with_empty_answer_returns_original() -> None:
    subtask = _plan(1, depends_on=[0])
    completed = {0: _result(0, "")}  # empty answer
    enriched = _enrich_subtask(subtask, completed)
    assert enriched.request == subtask.request


def test_deps_multiple_dependencies() -> None:
    subtask = _plan(2, depends_on=[0, 1])
    completed = {
        0: _result(0, "result A"),
        1: _result(1, "result B"),
    }
    enriched = _enrich_subtask(subtask, completed)
    assert "result A" in enriched.request
    assert "result B" in enriched.request


def test_context_truncated_to_max_chars() -> None:
    from reforge.runtime.orchestration.decomposition.async_runner import _MAX_CONTEXT_CHARS
    subtask = _plan(1, depends_on=[0])
    long_answer = "x" * (_MAX_CONTEXT_CHARS + 200)
    completed = {0: _result(0, long_answer)}
    enriched = _enrich_subtask(subtask, completed)
    # The injected snippet should be at most _MAX_CONTEXT_CHARS chars
    assert len(enriched.request) < len(subtask.request) + _MAX_CONTEXT_CHARS + 100
