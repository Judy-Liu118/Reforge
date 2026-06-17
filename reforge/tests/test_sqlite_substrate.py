"""Unit tests for SqliteMemorySubstrate.

Mirrors test_memory_substrate.py to verify protocol compliance + SQLite-specific
behaviour (concurrent writes, WAL, persistence across re-open).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from reforge.memory.models import MemoryRecord, MemoryType
from reforge.memory.sqlite_substrate import SqliteMemorySubstrate
from reforge.memory.substrate import MemorySubstrate


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def substrate(tmp_path: Path) -> SqliteMemorySubstrate:
    return SqliteMemorySubstrate(db_path=tmp_path / "test.db")


def _make_record(
    user_request: str = "analyze csv",
    outcome: str = "SUCCESS",
    memory_type: MemoryType = MemoryType.SUCCESS_PATTERN,
    error_type: str = "",
    recovery_action: str = "",
    retry_count: int = 0,
    memory_id: str = "",
) -> MemoryRecord:
    rec = MemoryRecord.from_session(
        session_id="test-session",
        user_request=user_request,
        outcome=outcome,
        retry_count=retry_count,
        error_type=error_type,
        recovery_action=recovery_action,
    )
    if memory_id:
        return rec.model_copy(update={"memory_id": memory_id, "memory_type": memory_type})
    return rec.model_copy(update={"memory_type": memory_type})


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_protocol_compliance(substrate: SqliteMemorySubstrate) -> None:
    assert isinstance(substrate, MemorySubstrate)


# ---------------------------------------------------------------------------
# Basic write / recall
# ---------------------------------------------------------------------------


def test_write_and_recall(substrate: SqliteMemorySubstrate) -> None:
    rec = _make_record(user_request="pandas csv analysis", outcome="SUCCESS")
    substrate.write(rec)

    results = substrate.recall("pandas csv", limit=5)
    assert any("csv" in r.user_request or "pandas" in r.user_request for r in results)


def test_empty_returns_empty(substrate: SqliteMemorySubstrate) -> None:
    assert substrate.recall("anything", limit=5) == []
    assert substrate.find_by_error("SomeError", limit=3) == []
    assert substrate.recall_for_planning("anything", limit=3) == []


def test_recall_scores_by_relevance(substrate: SqliteMemorySubstrate) -> None:
    """Most relevant record should appear first."""
    substrate.write(_make_record(user_request="read json file"))
    substrate.write(_make_record(user_request="pandas csv analysis"))
    substrate.write(_make_record(user_request="matplotlib plot"))

    results = substrate.recall("pandas csv analysis", limit=3)
    assert results[0].user_request == "pandas csv analysis"


# ---------------------------------------------------------------------------
# find_by_error
# ---------------------------------------------------------------------------


def test_find_by_error_exact(substrate: SqliteMemorySubstrate) -> None:
    rec = MemoryRecord(
        memory_id="r1",
        session_id="s1",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.RECOVERY,
        user_request="read file",
        outcome="RECOVERED",
        error_type="FileNotFoundError",
        retry_count=1,
    )
    substrate.write(rec)

    results = substrate.find_by_error("FileNotFoundError", limit=3)
    assert len(results) == 1
    assert results[0].error_type == "FileNotFoundError"


def test_find_by_error_no_match(substrate: SqliteMemorySubstrate) -> None:
    rec = MemoryRecord(
        memory_id="r2",
        session_id="s1",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.RECOVERY,
        user_request="divide",
        outcome="RECOVERED",
        error_type="ZeroDivisionError",
        retry_count=1,
    )
    substrate.write(rec)

    results = substrate.find_by_error("KeyError", limit=3)
    assert results == []


def test_find_by_error_only_returns_recovery_type(substrate: SqliteMemorySubstrate) -> None:
    """find_by_error must only return RECOVERY records."""
    substrate.write(MemoryRecord(
        memory_id="s1",
        session_id="x",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.SUCCESS_PATTERN,
        user_request="test",
        outcome="SUCCESS",
        error_type="ZeroDivisionError",
    ))
    results = substrate.find_by_error("ZeroDivisionError")
    assert results == []


# ---------------------------------------------------------------------------
# recall_for_planning
# ---------------------------------------------------------------------------


def test_recall_for_planning_excludes_failure(substrate: SqliteMemorySubstrate) -> None:
    substrate.write(MemoryRecord(
        memory_id="f1",
        session_id="s",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.FAILURE,
        user_request="load csv data",
        outcome="FAILED",
    ))
    substrate.write(_make_record(user_request="load csv data", outcome="SUCCESS"))

    results = substrate.recall_for_planning("load csv data", limit=5)
    for r in results:
        assert r.memory_type != MemoryType.FAILURE


def test_recall_for_planning_includes_recovery(substrate: SqliteMemorySubstrate) -> None:
    rec = MemoryRecord(
        memory_id="rv1",
        session_id="s",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.RECOVERY,
        user_request="parse json",
        outcome="RECOVERED",
        error_type="JSONDecodeError",
        retry_count=1,
    )
    substrate.write(rec)

    results = substrate.recall_for_planning("parse json", limit=3)
    assert any(r.memory_type == MemoryType.RECOVERY for r in results)


# ---------------------------------------------------------------------------
# Persistence: data survives closing + re-opening
# ---------------------------------------------------------------------------


def test_persistence_across_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "reopen_test.db"

    s1 = SqliteMemorySubstrate(db_path=db_path)
    s1.write(_make_record(user_request="persist this request", outcome="SUCCESS"))
    s1.close()

    s2 = SqliteMemorySubstrate(db_path=db_path)
    results = s2.recall("persist this request", limit=5)
    assert len(results) == 1
    assert results[0].user_request == "persist this request"
    s2.close()


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_returns_all_records(substrate: SqliteMemorySubstrate) -> None:
    for i in range(3):
        substrate.write(MemoryRecord(
            memory_id=f"m{i}",
            session_id="s",
            timestamp="2026-01-01T00:00:00",
            memory_type=MemoryType.SUCCESS_PATTERN,
            user_request=f"request {i}",
            outcome="SUCCESS",
        ))

    all_records = substrate.list_all()
    assert len(all_records) == 3


def test_list_all_filtered_by_type(substrate: SqliteMemorySubstrate) -> None:
    substrate.write(MemoryRecord(
        memory_id="r1",
        session_id="s",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.RECOVERY,
        user_request="recovery request",
        outcome="RECOVERED",
        retry_count=1,
    ))
    substrate.write(MemoryRecord(
        memory_id="s1",
        session_id="s",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.SUCCESS_PATTERN,
        user_request="success request",
        outcome="SUCCESS",
    ))

    recovery_only = substrate.list_all("RECOVERY")
    assert all(r.memory_type == MemoryType.RECOVERY for r in recovery_only)
    assert len(recovery_only) == 1


# ---------------------------------------------------------------------------
# Idempotent writes (INSERT OR REPLACE)
# ---------------------------------------------------------------------------


def test_write_same_id_replaces(substrate: SqliteMemorySubstrate) -> None:
    rec = _make_record(user_request="original", memory_id="fixed-id")
    substrate.write(rec)

    updated = rec.model_copy(update={"user_request": "updated"})
    substrate.write(updated)

    all_records = substrate.list_all()
    assert len(all_records) == 1
    assert all_records[0].user_request == "updated"
