"""Tests for ExecutionMemory record/recall, weighted scoring, and back-compat."""

from __future__ import annotations

from pathlib import Path


from reforge.memory.execution_memory import ExecutionMemory


def _mem(tmp_path: Path) -> ExecutionMemory:
    return ExecutionMemory(path=tmp_path / "exec_mem.jsonl")


def test_exact_failure_mode_match_scores_highest(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    mem.record(
        request="analyze csv file",
        outcome="RECOVERED",
        failure_mode="execution_error",
        repair_strategy="fix column name",
        problem_signature={"domain": "pandas", "root_cause": "missing_dataframe_column", "error_type": "KeyError"},
        error_type="KeyError",
    )
    mem.record(
        request="analyze csv file",
        outcome="FAILED",
        failure_mode="other_error",
        problem_signature={"domain": "general", "root_cause": "unknown", "error_type": "none"},
    )

    results = mem.recall_similar("analyze csv file", failure_mode="execution_error")
    assert len(results) >= 1
    assert results[0].failure_mode == "execution_error"


def test_signature_match_surfaces_partial_failure_mode(tmp_path: Path) -> None:
    """With matching problem_signature, records with non-exact failure_mode still appear."""
    mem = _mem(tmp_path)
    mem.record(
        request="load dataframe columns",
        outcome="RECOVERED",
        failure_mode="recoverable_intentional",
        problem_signature={"domain": "pandas", "root_cause": "missing_dataframe_column", "error_type": "KeyError"},
        error_type="KeyError",
    )

    results = mem.recall_similar(
        "read csv and select column",
        failure_mode="execution_error",
        problem_signature={"domain": "pandas", "root_cause": "missing_dataframe_column", "error_type": "KeyError"},
    )
    assert len(results) >= 1


def test_no_match_returns_empty_not_exception(tmp_path: Path) -> None:
    """No matching records returns empty list, never raises."""
    mem = _mem(tmp_path)
    mem.record(
        request="web scraping",
        outcome="SUCCESS",
        failure_mode="none",
    )
    results = mem.recall_similar("analyze pandas dataframe", failure_mode="execution_error")
    # May return empty or low-scored results — must not raise
    assert isinstance(results, list)


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    results = mem.recall_similar("anything", failure_mode="any")
    assert results == []


def test_top3_limit_respected(tmp_path: Path) -> None:
    mem = _mem(tmp_path)
    for i in range(5):
        mem.record(
            request=f"analyze csv {i}",
            outcome="SUCCESS",
            failure_mode="execution_error",
        )
    results = mem.recall_similar("analyze csv", failure_mode="execution_error")
    assert len(results) <= 3


def test_new_fields_backward_compat_on_old_records(tmp_path: Path) -> None:
    """Old JSONL lines without problem_signature/error_type still deserialize."""
    jsonl_path = tmp_path / "exec_mem.jsonl"
    old_line = (
        '{"timestamp":"2025-01-01T00:00:00Z","request":"old task","outcome":"SUCCESS",'
        '"failure_mode":"none","retryable":false,"repair_strategy":"","task_intent":""}\n'
    )
    jsonl_path.write_text(old_line, encoding="utf-8")

    mem = ExecutionMemory(path=jsonl_path)
    results = mem.recall_similar("old task", failure_mode="none")
    assert isinstance(results, list)


def test_recall_returns_repair_strategy(tmp_path: Path) -> None:
    """Exact failure_mode + matching keywords surfaces the record's repair_strategy."""
    mem = _mem(tmp_path)
    mem.record(
        request="read sales.csv, calculate profit average",
        outcome="RECOVERED", failure_mode="execution_error",
        retryable=True, repair_strategy="Check column names first",
    )
    mem.record(
        request="plot revenue chart",
        outcome="SUCCESS", failure_mode="none",
    )

    results = mem.recall_similar("read sales.csv", failure_mode="execution_error")
    assert len(results) == 1
    assert results[0].repair_strategy == "Check column names first"


def test_keyword_overlap_breaks_ties(tmp_path: Path) -> None:
    """When failure_mode matches both records, keyword overlap decides ordering."""
    mem = _mem(tmp_path)
    mem.record(
        request="read sales.csv",
        outcome="RECOVERED", failure_mode="execution_error",
        repair_strategy="strat A",
    )
    mem.record(
        request="plot chart revenue",
        outcome="SUCCESS", failure_mode="execution_error",
        repair_strategy="strat B",
    )

    results = mem.recall_similar("read csv", failure_mode="execution_error")
    assert len(results) >= 1
    assert results[0].repair_strategy == "strat A"
