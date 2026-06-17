"""Tests for TrajectoryStore and TrajectoryRecord."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reforge.runtime.infrastructure.trajectory.models import AttemptStep, TrajectoryRecord
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore


def _make_record(
    session_id: str = "sess-001",
    user_request: str = "analyze csv",
    final_outcome: str = "SUCCESS",
    recovery_chain: list[str] | None = None,
    problem_signature: dict | None = None,
) -> TrajectoryRecord:
    return TrajectoryRecord(
        trajectory_id="tid-001",
        session_id=session_id,
        timestamp="2026-01-01T00:00:00Z",
        user_request=user_request,
        task_intent="NORMAL_EXECUTION",
        total_attempts=1,
        final_outcome=final_outcome,
        outcome_reason="clean run",
        steps=[],
        problem_signature=problem_signature or {"domain": "pandas", "root_cause": "csv_analysis"},
        recovery_chain=recovery_chain or [],
    )


def test_save_and_list_all(tmp_path: Path) -> None:
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    rec = _make_record(session_id="s1")
    store.save(rec)

    all_recs = store.list_all()
    assert len(all_recs) == 1
    assert all_recs[0].session_id == "s1"


def test_find_by_session(tmp_path: Path) -> None:
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    store.save(_make_record(session_id="abc"))
    store.save(_make_record(session_id="xyz"))

    result = store.find_by_session("abc")
    assert result is not None
    assert result.session_id == "abc"


def test_find_by_session_missing(tmp_path: Path) -> None:
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    assert store.find_by_session("nope") is None


def test_list_all_empty_file(tmp_path: Path) -> None:
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    assert store.list_all() == []


def test_find_similar_keyword_match(tmp_path: Path) -> None:
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    store.save(_make_record(user_request="pandas csv sales analysis", final_outcome="SUCCESS"))
    store.save(_make_record(user_request="unrelated web scraping task", final_outcome="FAILED"))

    results = store.find_similar("analyze csv data", limit=3)
    assert len(results) >= 1
    assert any("csv" in r.user_request or "pandas" in r.user_request for r in results)


def test_find_similar_signature_match_outranks_keyword(tmp_path: Path) -> None:
    """Record with matching problem_signature should score higher than keyword-only match."""
    store = TrajectoryStore(path=tmp_path / "traj.jsonl")
    store.save(_make_record(
        user_request="something completely different",
        final_outcome="RECOVERED",
        problem_signature={"domain": "pandas", "root_cause": "missing_dataframe_column", "error_type": "KeyError"},
    ))
    store.save(_make_record(
        user_request="pandas csv pandas csv pandas",
        final_outcome="FAILED",
        problem_signature={"domain": "general", "root_cause": "unknown", "error_type": "none"},
    ))

    results = store.find_similar(
        "analyze dataframe columns",
        problem_signature={"domain": "pandas", "root_cause": "missing_dataframe_column", "error_type": "KeyError"},
        limit=2,
    )
    assert len(results) >= 1
    assert results[0].final_outcome == "RECOVERED"


def test_from_final_state_builds_record() -> None:
    """TrajectoryRecord.from_final_state produces a valid record from a mock state."""
    from reforge.runtime.domain.state.models import AttemptRecord, ReflectionResult

    state = MagicMock()
    state.attempts = [
        AttemptRecord(attempt=0, exit_code=1, duration_ms=120.0, error_type="KeyError"),
        AttemptRecord(attempt=1, exit_code=0, duration_ms=95.0, error_type=""),
    ]
    state.generated_code = "print('hello')"
    state.user_request = "analyze csv"
    state.semantic_state.task_intent = "NORMAL_EXECUTION"
    state.semantic_state.reflection_result = ReflectionResult(
        error_type="KeyError",
        error_summary="missing column",
        suggested_fix="use df['col'] not df.col",
    )
    state.semantic_state.evaluation_result = None
    state.outcome_state.task_outcome = "RECOVERED"
    state.outcome_state.outcome_reason = "recovered after retry"

    record = TrajectoryRecord.from_final_state(state, session_id="sess-xyz")

    assert record.session_id == "sess-xyz"
    assert record.total_attempts == 2
    assert record.final_outcome == "RECOVERED"
    assert len(record.steps) == 2
    assert record.recovery_chain == ["KeyError"]
    assert record.problem_signature.get("domain") is not None
