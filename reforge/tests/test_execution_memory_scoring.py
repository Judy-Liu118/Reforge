"""Tests for ExecutionMemory record/recall, weighted scoring, and back-compat."""

from __future__ import annotations

from pathlib import Path


from reforge.memory.execution_memory import ExecutionMemory


def _mem(tmp_path: Path) -> ExecutionMemory:
    return ExecutionMemory(path=tmp_path / "exec_mem.jsonl")


def test_empty_signature_falls_back_to_fingerprint(tmp_path: Path) -> None:
    """`{}` means "no signature", same as None — both derive one from error_type.

    Regression: the guard was `if sig is None`, but the production caller reads
    FailureSnapshot.problem_signature (default_factory=dict), which is `{}` and
    never None when reflection found no structural signal. Those records landed
    signature-less and could then only be recalled on a failure_mode collision,
    because admission requires a non-zero *structural* score.
    """
    mem = _mem(tmp_path)
    mem.record(
        request="load a module",
        outcome="RECOVERED",
        failure_mode="execution_error",
        repair_strategy="pip install pandas",
        problem_signature={},
        error_type="ModuleNotFoundError",
    )

    # A query with a *different* failure_mode must still match structurally —
    # only possible if the record carries a derived fingerprint, not `{}`.
    results = mem.recall_similar(
        "totally unrelated wording",
        failure_mode="recoverable_intentional",
        problem_signature={"error_class": "ModuleNotFoundError"},
    )
    assert len(results) == 1
    assert results[0].problem_signature["error_class"] == "ModuleNotFoundError"
    assert results[0].repair_strategy == "pip install pandas"


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


def test_text_overlap_alone_does_not_admit_a_record(tmp_path: Path) -> None:
    """Shared function words are not evidence that a stored repair applies.

    Without a structural admission gate, a verbose unrelated record rides into
    the top-3 on "the"/"and"/"of" alone — and ClassifyStage forwards
    records[0] as the repair_hint.
    """
    mem = _mem(tmp_path)
    mem.record(
        request="Please read the input and print the result of each of the items in the list "
                "and then print the summary of the report and each of the column totals",
        outcome="RECOVERED",
        failure_mode="timeout",
        repair_strategy="JUNK — unrelated to any KeyError",
        problem_signature={"error_class": "TimeoutError", "domain": "python",
                           "root_cause": "timeout"},
    )

    results = mem.recall_similar(
        "Read demo_orders.csv and print the order_id of each row.",
        failure_mode="execution_error",
        problem_signature={"error_class": "KeyError", "domain": "general",
                           "root_cause": "missing_key", "missing_key": "order_id"},
    )
    assert results == []


def test_verbose_request_does_not_outrank_a_concise_match(tmp_path: Path) -> None:
    """Dice normalisation means padding a request lowers its score.

    Both records match structurally; the concise one shares fewer words in
    absolute terms but far more as a fraction of its text.
    """
    mem = _mem(tmp_path)
    sig = {"error_class": "KeyError", "domain": "general", "root_cause": "missing_key"}
    mem.record(
        request="read the csv and print the sum and then print each of the rows and the totals "
                "and the summary of the report and each of the column names in the table",
        outcome="RECOVERED", failure_mode="execution_error",
        repair_strategy="verbose", problem_signature=sig,
    )
    mem.record(
        request="read demo.csv print the sum",
        outcome="RECOVERED", failure_mode="execution_error",
        repair_strategy="concise", problem_signature=sig,
    )

    results = mem.recall_similar(
        "read demo.csv print the sum", failure_mode="execution_error",
        problem_signature=sig,
    )
    assert results[0].repair_strategy == "concise"


def test_persisted_error_type_alias_still_matches_on_error_class(tmp_path: Path) -> None:
    """Signatures written before the error_type alias was dropped stay recallable.

    Those records carry both keys; the scorer no longer weights error_type, but
    error_class was always written alongside it, so the match survives.
    """
    mem = _mem(tmp_path)
    mem.record(
        request="read demo.csv and sum the revenue column",
        outcome="RECOVERED",
        failure_mode="execution_error",
        repair_strategy="introspect df.columns first",
        # Legacy shape: error_type present as an alias of error_class.
        problem_signature={
            "error_class": "KeyError", "error_type": "KeyError",
            "domain": "pandas", "root_cause": "missing_key", "missing_key": "revenue",
        },
        error_type="KeyError",
    )

    # Query signature in the current shape — no error_type key at all.
    results = mem.recall_similar(
        "read demo.csv and sum the revenue column",
        failure_mode="execution_error",
        problem_signature={
            "error_class": "KeyError", "domain": "pandas",
            "root_cause": "missing_key", "missing_key": "revenue",
        },
    )
    assert len(results) == 1
    assert results[0].repair_strategy == "introspect df.columns first"


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
