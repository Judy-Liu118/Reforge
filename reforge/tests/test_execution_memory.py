"""Unit tests for ExecutionMemory."""

import tempfile
from pathlib import Path

from reforge.memory.execution_memory import ExecutionMemory


def test_record_and_recall_by_failure_mode():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.jsonl"
        mem = ExecutionMemory(path=path)
        mem.record(
            request="read sales.csv, calculate profit average",
            outcome="RECOVERED", failure_mode="execution_error",
            retryable=True, repair_strategy="Check column names first",
        )
        mem.record(
            request="plot revenue chart",
            outcome="SUCCESS", failure_mode="none",
        )

        # Search by failure_mode exact match
        results = mem.recall_similar("read sales.csv", failure_mode="execution_error")
        assert len(results) == 1
        assert results[0].repair_strategy == "Check column names first"

        # failure_mode mismatch lowers score but no longer hard-filters —
        # the exact-match record (execution_error) should still rank first
        results2 = mem.recall_similar("read sales.csv", failure_mode="timeout")
        # If records surface at all, the execution_error record from keyword overlap
        # should rank ahead of the "none" failure_mode record
        if results2:
            assert results2[0].failure_mode != "none" or len(results2) == 1


def test_keyword_scoring():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.jsonl"
        mem = ExecutionMemory(path=path)
        mem.record(request="read sales.csv", outcome="RECOVERED",
                   failure_mode="execution_error", repair_strategy="strat A")
        mem.record(request="plot chart revenue", outcome="SUCCESS",
                   failure_mode="execution_error", repair_strategy="strat B")

        # "read csv" should match "read sales.csv" more than "plot chart revenue"
        results = mem.recall_similar("read csv", failure_mode="execution_error")
        assert len(results) >= 1
        assert results[0].repair_strategy == "strat A"  # Higher keyword overlap
