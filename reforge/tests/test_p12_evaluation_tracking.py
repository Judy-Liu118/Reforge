"""Tests for P12 per-attempt evaluation tracking."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from reforge.runtime.domain.state.models import (
    AttemptRecord,
    EvalCheck,
    EvaluationResult,
    ExecutionState,
    RuntimeState,
)


# --- AttemptRecord new fields ---

def test_attempt_record_has_eval_fields() -> None:
    rec = AttemptRecord(attempt=0, exit_code=0, duration_ms=100.0, error_type="")
    assert rec.eval_score == 1.0
    assert rec.eval_failure_type == ""


def test_attempt_record_eval_fields_settable() -> None:
    rec = AttemptRecord(
        attempt=0,
        exit_code=1,
        duration_ms=120.0,
        error_type="KeyError",
        eval_score=0.4,
        eval_failure_type="empty_output",
    )
    assert rec.eval_score == 0.4
    assert rec.eval_failure_type == "empty_output"


def test_attempt_record_backward_compat_old_jsonl() -> None:
    """Old JSONL without eval fields still deserializes (default values applied)."""
    old_json = '{"attempt": 0, "exit_code": 0, "duration_ms": 100.0, "error_type": ""}'
    rec = AttemptRecord.model_validate_json(old_json)
    assert rec.eval_score == 1.0
    assert rec.eval_failure_type == ""


# --- evaluation_node writes eval to last AttemptRecord ---

def _make_state_with_attempt(stdout: str = "result", exit_code: int = 0) -> RuntimeState:
    state = RuntimeState(
        user_request="analyze data",
        exec_state=ExecutionState(stdout=stdout, stderr="", exit_code=exit_code, duration_ms=100.0),
        attempts=[
            AttemptRecord(attempt=0, exit_code=exit_code, duration_ms=100.0, error_type=""),
        ],
    )
    return state


def test_evaluation_node_annotates_last_attempt(tmp_path: Path) -> None:
    """evaluation_node must update last AttemptRecord with eval_score."""
    from reforge.runtime.orchestration.graph.nodes.evaluation import evaluation_node

    state = _make_state_with_attempt(stdout="The mean is 42.5", exit_code=0)
    result = evaluation_node(state)

    assert "attempts" in result
    attempts = [AttemptRecord.model_validate(a) for a in result["attempts"]]
    assert len(attempts) == 1
    # Score should be between 0 and 1
    assert 0.0 <= attempts[0].eval_score <= 1.0


def test_evaluation_node_runs_on_failed_execution() -> None:
    """evaluation_node must run even when execution failed (traceback exists)."""
    from reforge.runtime.orchestration.graph.nodes.evaluation import evaluation_node

    state = RuntimeState(
        user_request="analyze data",
        exec_state=ExecutionState(
            stdout="", stderr="Traceback (most recent call last):\n  ...\nKeyError: 'col'",
            exit_code=1, duration_ms=100.0,
        ),
        attempts=[AttemptRecord(attempt=0, exit_code=1, duration_ms=100.0, error_type="KeyError")],
    )
    result = evaluation_node(state)

    # Must return evaluation result — NOT a dummy passed=True
    er = result["evaluation_result"]
    assert "score" in er
    assert "failure_type" in er
    # A failed execution with empty stdout should have score < 1.0
    assert er["score"] < 1.0

    # Last attempt must be annotated
    attempts = [AttemptRecord.model_validate(a) for a in result["attempts"]]
    assert attempts[-1].eval_score == er["score"]
    assert attempts[-1].eval_failure_type == er["failure_type"]


def test_evaluation_node_no_attempts_does_not_crash() -> None:
    """evaluation_node is safe when state.attempts is empty."""
    from reforge.runtime.orchestration.graph.nodes.evaluation import evaluation_node

    state = RuntimeState(
        user_request="test",
        exec_state=ExecutionState(stdout="hi there output", stderr="", exit_code=0),
    )
    result = evaluation_node(state)
    assert "evaluation_result" in result
    assert result["attempts"] == []


# --- _eval_trend helper ---

def test_eval_trend_single_attempt() -> None:
    from reforge.cli.main import _eval_trend
    attempts = [AttemptRecord(attempt=0, eval_score=0.9)]
    assert _eval_trend(attempts) == "0.90"


def test_eval_trend_multiple_attempts() -> None:
    from reforge.cli.main import _eval_trend
    attempts = [
        AttemptRecord(attempt=0, eval_score=0.4),
        AttemptRecord(attempt=1, eval_score=0.7),
        AttemptRecord(attempt=2, eval_score=1.0),
    ]
    assert _eval_trend(attempts) == "0.40→0.70→1.00"


def test_eval_trend_empty_attempts() -> None:
    from reforge.cli.main import _eval_trend
    assert _eval_trend([]) == "-"
