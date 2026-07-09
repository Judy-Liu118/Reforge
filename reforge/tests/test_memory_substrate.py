"""Tests for MemorySubstrate Protocol and CompositeMemorySubstrate."""

from __future__ import annotations

from pathlib import Path


from reforge.memory.models import MemoryRecord, MemoryType
from reforge.memory.store import MemoryStore
from reforge.memory.substrate import CompositeMemorySubstrate, MemorySubstrate


def _make_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(base_dir=tmp_path / "memory")


def _make_record(
    user_request: str = "analyze csv",
    outcome: str = "SUCCESS",
    memory_type: MemoryType = MemoryType.SUCCESS_PATTERN,
    error_type: str = "",
    recovery_action: str = "",
) -> MemoryRecord:
    return MemoryRecord.from_session(
        session_id="test-session",
        user_request=user_request,
        outcome=outcome,
        retry_count=0,
        error_type=error_type,
        recovery_action=recovery_action,
    )


def test_protocol_compliance(tmp_path: Path) -> None:
    """CompositeMemorySubstrate satisfies the MemorySubstrate Protocol."""
    store = _make_store(tmp_path)
    substrate = CompositeMemorySubstrate(store=store)
    assert isinstance(substrate, MemorySubstrate)


def test_write_and_recall(tmp_path: Path) -> None:
    """write() persists a record, recall() retrieves it."""
    store = _make_store(tmp_path)
    substrate = CompositeMemorySubstrate(store=store)

    record = _make_record(user_request="pandas csv analysis", outcome="SUCCESS")
    substrate.write(record)

    results = substrate.recall("pandas csv", limit=5)
    assert any("csv" in r.user_request or "pandas" in r.user_request for r in results)


def test_empty_memory_returns_empty(tmp_path: Path) -> None:
    """recall() on empty memory returns an empty list, no exception."""
    store = _make_store(tmp_path)
    substrate = CompositeMemorySubstrate(store=store)

    results = substrate.recall("any query", limit=5)
    assert results == []


def test_recall_for_planning_filters_type(tmp_path: Path) -> None:
    """recall_for_planning returns only SUCCESS_PATTERN and RECOVERY records."""
    store = _make_store(tmp_path)
    substrate = CompositeMemorySubstrate(store=store)

    # Write one of each type
    success_rec = _make_record(user_request="load csv data", outcome="SUCCESS")
    substrate.write(success_rec)

    failure_rec = MemoryRecord(
        memory_id="f1",
        session_id="s1",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.FAILURE,
        user_request="load csv data",
        outcome="FAILED",
    )
    substrate.write(failure_rec)

    results = substrate.recall_for_planning("load csv data", limit=5)
    # FAILURE records should not appear
    for r in results:
        assert r.memory_type != MemoryType.FAILURE


def test_find_by_error_delegates_to_retriever(tmp_path: Path) -> None:
    """find_by_error() returns RECOVERY records matching the error type."""
    store = _make_store(tmp_path)
    substrate = CompositeMemorySubstrate(store=store)

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
    assert len(results) >= 1
    assert any("FileNotFoundError" in r.error_type for r in results)


# ---------------------------------------------------------------------------
# recall_repair_pattern — cross-task repair transfer
# ---------------------------------------------------------------------------


def _recovery(memory_id: str, *, signature: dict, recovery_action: str = "",
              user_request: str = "") -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        session_id="s",
        timestamp="2026-01-01T00:00:00",
        memory_type=MemoryType.RECOVERY,
        user_request=user_request,
        outcome="RECOVERED",
        error_type=signature.get("error_class", ""),
        recovery_action=recovery_action,
        retry_count=1,
        problem_signature=signature,
    )


class TestRecallRepairPattern:
    """Pins the structural-fingerprint repair-pattern recall.

    Why this matters separately from `recall()`:
      - `recall(query)` matches against a free-form text string and over-
        weights keyword overlap with `user_request` / `tags` / reflection.
      - `recall_repair_pattern(signature)` ignores text entirely and ranks
        by overlap of *typed fingerprint fields*. Two different user tasks
        that crash with the same `KeyError("revenue")` should hit the same
        repair record — which keyword recall would miss because the task
        wording differs.
    """

    def test_empty_signature_returns_empty(self, tmp_path: Path) -> None:
        substrate = CompositeMemorySubstrate(store=_make_store(tmp_path))
        substrate.write(_recovery("r1", signature={"error_class": "KeyError"}))
        assert substrate.recall_repair_pattern({}, limit=3) == []

    def test_empty_store_returns_empty(self, tmp_path: Path) -> None:
        substrate = CompositeMemorySubstrate(store=_make_store(tmp_path))
        assert substrate.recall_repair_pattern({"error_class": "KeyError"}, limit=3) == []

    def test_only_recovery_records_considered(self, tmp_path: Path) -> None:
        """A FAILURE record with the same signature must not be returned —
        we want known-working repairs, not past dead-ends."""
        substrate = CompositeMemorySubstrate(store=_make_store(tmp_path))
        substrate.write(MemoryRecord(
            memory_id="f1",
            session_id="s",
            timestamp="2026-01-01T00:00:00",
            memory_type=MemoryType.FAILURE,
            user_request="something",
            outcome="FAILED",
            error_type="KeyError",
            problem_signature={"error_class": "KeyError", "missing_key": "revenue"},
        ))
        result = substrate.recall_repair_pattern(
            {"error_class": "KeyError", "missing_key": "revenue"}, limit=3
        )
        assert result == []

    def test_ranks_by_structural_overlap(self, tmp_path: Path) -> None:
        substrate = CompositeMemorySubstrate(store=_make_store(tmp_path))
        # Three RECOVERY records of varying overlap with the query.
        substrate.write(_recovery(
            "r_full",
            signature={
                "error_class": "KeyError",
                "missing_key": "revenue",
                "domain": "pandas",
            },
            recovery_action="rename column",
        ))
        substrate.write(_recovery(
            "r_error_only",
            signature={"error_class": "KeyError", "missing_key": "profit"},
            recovery_action="use .get()",
        ))
        substrate.write(_recovery(
            "r_unrelated",
            signature={"error_class": "NameError", "undefined_name": "foo"},
            recovery_action="import missing",
        ))

        results = substrate.recall_repair_pattern(
            {"error_class": "KeyError", "missing_key": "revenue", "domain": "pandas"},
            limit=3,
        )
        # The full-overlap record wins; the unrelated error_class is filtered out.
        ids = [r.memory_id for r in results]
        assert ids[0] == "r_full"
        assert "r_unrelated" not in ids

    def test_cross_task_transfer(self, tmp_path: Path) -> None:
        """Two different user requests, same crash → same repair found.

        The point of structural matching: text-keyword recall would miss
        this hit because `analyze sales` shares no words with `compute
        revenue Q4`.
        """
        substrate = CompositeMemorySubstrate(store=_make_store(tmp_path))
        substrate.write(_recovery(
            "r1",
            signature={"error_class": "KeyError", "missing_key": "revenue"},
            recovery_action="rename column to revenue",
            user_request="analyze sales",
        ))
        # New failure on a totally different task — but the fingerprint is
        # the same shape.
        results = substrate.recall_repair_pattern(
            {"error_class": "KeyError", "missing_key": "revenue"}, limit=3,
        )
        assert len(results) == 1
        assert results[0].recovery_action == "rename column to revenue"


# ---------------------------------------------------------------------------
# Sqlite parity
# ---------------------------------------------------------------------------


class TestRecallRepairPatternSqlite:
    """The SQLite backend must produce identical ranking to the in-memory one."""

    def test_sqlite_backend_matches_composite_ranking(self, tmp_path: Path) -> None:
        from reforge.memory.sqlite_substrate import SqliteMemorySubstrate

        sub = SqliteMemorySubstrate(db_path=tmp_path / "mem.db")
        try:
            sub.write(_recovery(
                "r_full",
                signature={
                    "error_class": "KeyError",
                    "missing_key": "revenue",
                    "domain": "pandas",
                },
            ))
            sub.write(_recovery(
                "r_partial",
                signature={"error_class": "KeyError"},
            ))
            sub.write(_recovery(
                "r_unrelated",
                signature={"error_class": "NameError"},
            ))
            results = sub.recall_repair_pattern(
                {"error_class": "KeyError", "missing_key": "revenue", "domain": "pandas"},
                limit=3,
            )
            ids = [r.memory_id for r in results]
            assert ids[0] == "r_full"
            assert "r_unrelated" not in ids
        finally:
            sub.close()

    def test_sqlite_empty_signature(self, tmp_path: Path) -> None:
        from reforge.memory.sqlite_substrate import SqliteMemorySubstrate

        sub = SqliteMemorySubstrate(db_path=tmp_path / "mem.db")
        try:
            assert sub.recall_repair_pattern({}, limit=3) == []
        finally:
            sub.close()
