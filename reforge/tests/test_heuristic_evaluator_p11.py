"""Tests for P11 HeuristicEvaluator additions: retry_drift and output_contains_data."""

from __future__ import annotations

import time
from unittest.mock import MagicMock


from reforge.runtime.orchestration.evaluation.heuristics import HeuristicEvaluator
from reforge.runtime.domain.state.models import (
    AttemptRecord,
    EvaluationResult,
    ExecutionState,
    ReflectionResult,
    RuntimeState,
    SemanticState,
)


def _state(
    stdout: str = "some output",
    exit_code: int = 0,
    user_request: str = "analyze csv data",
    attempts: list[AttemptRecord] | None = None,
    reflection_error_type: str = "",
    retry_count: int = 0,
) -> RuntimeState:
    rr = (
        ReflectionResult(
            error_type=reflection_error_type,
            error_summary=f"{reflection_error_type} occurred",
            suggested_fix="fix it",
        )
        if reflection_error_type else None
    )
    state = RuntimeState(
        user_request=user_request,
        exec_state=ExecutionState(stdout=stdout, stderr="", exit_code=exit_code),
        semantic_state=SemanticState(reflection_result=rr),
    )
    state.attempts = attempts or []
    return state


# --- retry_drift check ---

def test_retry_drift_detected_when_same_error_repeats() -> None:
    attempts = [
        AttemptRecord(attempt=0, exit_code=1, error_type="KeyError"),
        AttemptRecord(attempt=1, exit_code=1, error_type="KeyError"),
    ]
    state = _state(
        stdout="x",
        exit_code=1,
        attempts=attempts,
        reflection_error_type="KeyError",
        retry_count=2,
    )
    er = HeuristicEvaluator().evaluate(state)
    drift_checks = [c for c in er.checks if c.name == "retry_drift"]
    assert len(drift_checks) == 1
    assert not drift_checks[0].passed


def test_retry_drift_not_flagged_when_errors_differ() -> None:
    attempts = [
        AttemptRecord(attempt=0, exit_code=1, error_type="KeyError"),
        AttemptRecord(attempt=1, exit_code=1, error_type="ValueError"),
    ]
    state = _state(
        stdout="x",
        exit_code=1,
        attempts=attempts,
        reflection_error_type="ValueError",
        retry_count=2,
    )
    er = HeuristicEvaluator().evaluate(state)
    drift_checks = [c for c in er.checks if c.name == "retry_drift"]
    assert len(drift_checks) == 0


def test_retry_drift_not_flagged_on_first_attempt() -> None:
    attempts = [AttemptRecord(attempt=0, exit_code=1, error_type="KeyError")]
    state = _state(
        stdout="x",
        exit_code=1,
        attempts=attempts,
        reflection_error_type="KeyError",
        retry_count=1,
    )
    er = HeuristicEvaluator().evaluate(state)
    drift_checks = [c for c in er.checks if c.name == "retry_drift"]
    assert len(drift_checks) == 0


def test_retry_drift_not_flagged_for_intentional_task() -> None:
    attempts = [
        AttemptRecord(attempt=0, exit_code=1, error_type="SyntaxError"),
        AttemptRecord(attempt=1, exit_code=1, error_type="SyntaxError"),
    ]
    state = _state(
        stdout="x",
        exit_code=1,
        user_request="故意报错演示 traceback",
        attempts=attempts,
        reflection_error_type="SyntaxError",
        retry_count=2,
    )
    er = HeuristicEvaluator().evaluate(state)
    drift_checks = [c for c in er.checks if c.name == "retry_drift"]
    assert len(drift_checks) == 0


# --- output_contains_data check ---

def test_output_contains_data_fails_for_brief_data_task_output() -> None:
    state = _state(stdout="ok", user_request="calculate the mean of csv data")
    er = HeuristicEvaluator().evaluate(state)
    data_checks = [c for c in er.checks if c.name == "output_contains_data"]
    assert len(data_checks) == 1
    assert not data_checks[0].passed


def test_output_contains_data_passes_when_output_has_digits() -> None:
    state = _state(stdout="42.5", user_request="calculate mean from csv")
    er = HeuristicEvaluator().evaluate(state)
    data_checks = [c for c in er.checks if c.name == "output_contains_data"]
    assert len(data_checks) == 0


def test_output_contains_data_passes_when_output_is_long_enough() -> None:
    state = _state(stdout="The result is a computed statistic", user_request="analyze csv data")
    er = HeuristicEvaluator().evaluate(state)
    data_checks = [c for c in er.checks if c.name == "output_contains_data"]
    assert len(data_checks) == 0


def test_output_contains_data_not_checked_for_non_data_task() -> None:
    state = _state(stdout="hi", user_request="print hello world")
    er = HeuristicEvaluator().evaluate(state)
    data_checks = [c for c in er.checks if c.name == "output_contains_data"]
    assert len(data_checks) == 0


# --- compare_swallowed: low score + Warning + exit 0 means try/except swallow ---


def test_swallowed_compare_failure_is_caught(tmp_path) -> None:
    """Regression: LLM wrapped compare_images in try/except, printed a
    Warning about low score, and let the script exit cleanly. Eval used
    to give 100% PASS; now must flag compare_swallowed."""
    stdout = (
        "Generated HTML written to index.html\n"
        "Successfully rendered HTML to current.png\n"
        "\n"
        "Visual similarity score: 0.25\n"
        "Warning: Low similarity detected. Differences:\n"
        "  - text mismatches, color drift, missing sections\n"
    )
    state = _state(
        stdout=stdout,
        exit_code=0,
        user_request="复刻 target.png 的页面",
    )
    er = HeuristicEvaluator().evaluate(state)
    assert _has_failed(er, "compare_swallowed")
    assert er.failure_type == "swallowed_comparison"


def test_high_score_with_warning_text_is_not_flagged(tmp_path) -> None:
    """If the score is well above threshold, mentions of 'warning' are
    benign commentary, not swallow."""
    stdout = (
        "Visual similarity score: 0.92\n"
        "Warning: this build was the second attempt.\n"
    )
    state = _state(stdout=stdout, exit_code=0)
    er = HeuristicEvaluator().evaluate(state)
    assert not any(c.name == "compare_swallowed" for c in er.checks)


def test_low_score_alone_without_warning_is_not_flagged(tmp_path) -> None:
    """A printed low score by itself isn't proof of swallow — the script
    could be inside a normal codepath about to raise."""
    stdout = "Visual similarity score: 0.30\n"
    state = _state(stdout=stdout, exit_code=0)
    er = HeuristicEvaluator().evaluate(state)
    assert not any(c.name == "compare_swallowed" for c in er.checks)


def test_warning_without_score_is_not_flagged(tmp_path) -> None:
    stdout = "Warning: something else happened\n"
    state = _state(stdout=stdout, exit_code=0)
    er = HeuristicEvaluator().evaluate(state)
    assert not any(c.name == "compare_swallowed" for c in er.checks)


def test_swallow_skipped_when_script_already_failed(tmp_path) -> None:
    """If the script raised (exit_code != 0), the compare wasn't swallowed
    even if Warning + low score appear in stdout."""
    stdout = "Visual similarity score: 0.25\nWarning: low\n"
    state = _state(stdout=stdout, exit_code=1)
    er = HeuristicEvaluator().evaluate(state)
    assert not any(c.name == "compare_swallowed" for c in er.checks)


def test_swallow_skipped_for_intentional_task(tmp_path) -> None:
    stdout = "Visual similarity score: 0.20\nWarning: low\n"
    state = _state(
        stdout=stdout,
        exit_code=0,
        user_request="故意触发 low similarity warning 演示 error example",
    )
    er = HeuristicEvaluator().evaluate(state)
    assert not any(c.name == "compare_swallowed" for c in er.checks)


def test_comparison_skipped_message_caught(tmp_path) -> None:
    """Another swallow variant — 'Comparison skipped due to missing target'."""
    stdout = (
        "Visual similarity score: 0.10\n"
        "Comparison skipped due to network issues.\n"
    )
    state = _state(stdout=stdout, exit_code=0)
    er = HeuristicEvaluator().evaluate(state)
    assert _has_failed(er, "compare_swallowed")


# --- clean_exit: exit_code != 0 is failure even without a traceback ---


def test_clean_exit_fails_on_nonzero_exit_even_with_clean_stdout(tmp_path) -> None:
    """Regression: a script that prints 'Error: ...' and calls exit(1) had
    eval scoring 100% because stderr was empty and stdout had no traceback
    shape. The governor caught it via exit_code, but eval/reflection were
    silently agreeing it succeeded — incoherent signal."""
    state = _state(
        stdout="Error: 'orders.csv' not found.",
        exit_code=1,
        user_request="Read orders.csv and print a summary",
    )
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert _has_failed(er, "clean_exit")
    assert er.failure_type == "execution_failed"


def test_clean_exit_passes_on_zero_exit(tmp_path) -> None:
    state = _state(stdout="result: 42", exit_code=0)
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert not _has_failed(er, "clean_exit")
    assert not any(c.name == "clean_exit" for c in er.checks)


def test_clean_exit_fails_alongside_real_traceback(tmp_path) -> None:
    state = _state(
        stdout="",
        exit_code=1,
        user_request="do thing",
    )
    state.exec_state.stderr = "Traceback (most recent call last):\n  ValueError: bad"
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert _has_failed(er, "clean_exit")


# --- no_error_in_output: pattern-based, not substring scan ---


def _has_failed(er, name: str) -> bool:
    return any(c.name == name and not c.passed for c in er.checks)


def test_no_error_in_output_passes_when_prose_contains_word_error() -> None:
    """Regression: 'absolute error vs math.pi: 0.01' must not trip the check.

    Before the fix, ERROR_KEYWORDS was a substring list including 'error', so
    any task that legitimately printed the word — Monte Carlo estimates,
    statistics reports, accuracy summaries — was wrongly flagged.
    """
    stdout = (
        "Estimated pi: 3.15184\n"
        "Absolute error vs math.pi: 0.010247346410206859"
    )
    er = HeuristicEvaluator().evaluate(_state(stdout=stdout, user_request="estimate pi via monte carlo"))
    assert not _has_failed(er, "no_error_in_output")


def test_no_error_in_output_passes_for_other_benign_prose() -> None:
    """Substrings 'cannot', 'failed', 'exception', 'not found' in prose are fine."""
    samples = [
        "We cannot reject the null hypothesis at alpha=0.05.",
        "The test for normality failed to detect skew below 0.1.",
        "Exception handling is covered in chapter 7 of the docs.",
        "Pattern 'foo' was not found in 3/10 documents (false positive rate stable).",
    ]
    for stdout in samples:
        er = HeuristicEvaluator().evaluate(_state(stdout=stdout, user_request="run analysis"))
        assert not _has_failed(er, "no_error_in_output"), f"false positive on: {stdout!r}"


def test_no_error_in_output_fails_on_real_traceback() -> None:
    stdout = (
        'Traceback (most recent call last):\n'
        '  File "x.py", line 4, in <module>\n'
        "    1 / 0\n"
        "ZeroDivisionError: division by zero"
    )
    er = HeuristicEvaluator().evaluate(_state(stdout=stdout, user_request="divide two numbers"))
    assert _has_failed(er, "no_error_in_output")


def test_no_error_in_output_fails_on_typed_exception_line() -> None:
    """A lone 'ValueError: bad input' is still a real error signal."""
    er = HeuristicEvaluator().evaluate(
        _state(stdout="ValueError: bad input", user_request="parse the value")
    )
    assert _has_failed(er, "no_error_in_output")


def test_no_error_in_output_fails_on_file_not_found_message() -> None:
    er = HeuristicEvaluator().evaluate(
        _state(
            stdout="open('x.csv'): No such file or directory",
            user_request="read csv",
        )
    )
    assert _has_failed(er, "no_error_in_output")


def test_no_error_in_output_fails_on_permission_denied() -> None:
    er = HeuristicEvaluator().evaluate(
        _state(stdout="cp x y: Permission denied", user_request="copy file")
    )
    assert _has_failed(er, "no_error_in_output")


def test_no_error_in_output_relaxed_for_intentional_task() -> None:
    """When the user asked for a demo error, prose mentioning errors still passes."""
    stdout = "Traceback (most recent call last):\nValueError: demo"
    er = HeuristicEvaluator().evaluate(
        _state(stdout=stdout, user_request="故意触发 ValueError 演示 traceback")
    )
    assert not _has_failed(er, "no_error_in_output")


# --- output_artifact_exists: prompt-promised files must be produced ---


def test_artifact_check_passes_when_promised_file_exists(tmp_path) -> None:
    (tmp_path / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    state = _state(
        stdout="Saved chart with 1000 points",
        user_request="Estimate pi via Monte Carlo and save a scatter plot to plot.png",
    )
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert not _has_failed(er, "output_artifact_exists")


def test_artifact_check_fails_when_promised_file_missing(tmp_path) -> None:
    state = _state(
        stdout="Error: orders.csv not found — exiting cleanly",
        user_request="Read orders.csv and save the report to q1_report.md",
    )
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert _has_failed(er, "output_artifact_exists")
    assert er.failure_type == "missing_artifact"


def test_artifact_check_fails_when_promised_file_is_empty(tmp_path) -> None:
    (tmp_path / "out.csv").touch()  # exists but zero bytes
    state = _state(
        stdout="done",
        user_request="Write the cleaned data to out.csv",
    )
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert _has_failed(er, "output_artifact_exists")


def test_artifact_check_skipped_when_no_promise_in_prompt(tmp_path) -> None:
    """Prompts without a 'save/write X to FILE.ext' phrase get no check."""
    for prompt in [
        "Estimate pi using Monte Carlo and print the result.",
        "Compute the mean of these numbers: 1,2,3.",
        "Tell me about NumPy.",
    ]:
        er = HeuristicEvaluator(workspace=tmp_path).evaluate(
            _state(stdout="result: 3.14", user_request=prompt)
        )
        assert not any(c.name == "output_artifact_exists" for c in er.checks), \
            f"unexpected artifact check on prompt: {prompt!r}"


def test_artifact_check_skipped_when_execution_failed(tmp_path) -> None:
    """If exit_code != 0 the failure is already caught upstream; the artifact
    check would just add noise."""
    state = _state(
        stdout="",
        exit_code=1,
        user_request="Save the report to result.md",
    )
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert not any(c.name == "output_artifact_exists" for c in er.checks)


def test_artifact_check_skipped_for_intentional_task(tmp_path) -> None:
    state = _state(
        stdout="x",
        user_request="故意触发 ValueError 演示 traceback，并 save the log to err.log",
    )
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert not any(c.name == "output_artifact_exists" for c in er.checks)


def test_artifact_check_handles_multiple_promises(tmp_path) -> None:
    (tmp_path / "a.png").write_text("x")
    # b.csv is missing
    state = _state(
        stdout="ok",
        user_request="Save the chart to a.png and write the table to b.csv",
    )
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert _has_failed(er, "output_artifact_exists")


def test_artifact_check_fails_when_file_is_stale_from_prior_attempt(tmp_path) -> None:
    """Regression: attempt N writes the file, attempt N+1 exits without
    writing it — eval must still fail because the file is stale relative
    to attempt N+1's execution window.
    """
    import os
    f = tmp_path / "report.md"
    f.write_text("# stale report from a prior attempt")
    # Force mtime well into the past (5 minutes ago)
    stale_time = time.time() - 300
    os.utime(f, (stale_time, stale_time))

    state = _state(
        stdout="File not found — exiting cleanly",
        user_request="Read orders.csv and save the report to report.md",
    )
    # Pin "now" so the freshness window is deterministic
    er = HeuristicEvaluator(workspace=tmp_path, now_fn=time.time).evaluate(state)
    assert _has_failed(er, "output_artifact_exists")
    failed = next(c for c in er.checks if c.name == "output_artifact_exists")
    assert "stale" in failed.detail.lower()


def test_artifact_check_passes_when_file_is_freshly_written(tmp_path) -> None:
    """Counter to the stale check: a file written *now* (within the freshness
    window) is accepted, even though the same prompt asked for it."""
    f = tmp_path / "report.md"
    f.write_text("# fresh report")
    state = _state(
        stdout="Wrote report",
        user_request="Save the summary to report.md",
    )
    er = HeuristicEvaluator(workspace=tmp_path).evaluate(state)
    assert not _has_failed(er, "output_artifact_exists")


def test_artifact_check_extracts_only_known_extensions() -> None:
    """Don't match prose like 'send it to John' or 'go to the store'."""
    evaluator = HeuristicEvaluator()
    assert evaluator._extract_promised_artifacts("send the report to john") == []
    assert evaluator._extract_promised_artifacts("save it to chart.png") == ["chart.png"]
    assert evaluator._extract_promised_artifacts(
        "save the plot to chart.png and the data to results.csv"
    ) == ["chart.png", "results.csv"]


# --- AttemptStep eval fields in TrajectoryRecord ---

def test_trajectory_attempt_step_has_eval_fields() -> None:
    from reforge.runtime.infrastructure.trajectory.models import TrajectoryRecord

    mock_state = MagicMock()
    mock_state.attempts = [AttemptRecord(attempt=0, exit_code=0, duration_ms=100, error_type="")]
    mock_state.generated_code = "print('hi')"
    mock_state.user_request = "test"
    mock_state.semantic_state.task_intent = "NORMAL_EXECUTION"
    mock_state.semantic_state.reflection_result = None
    mock_state.semantic_state.evaluation_result = EvaluationResult(
        passed=True, score=0.9, checks=[], summary="ok", failure_type=""
    )
    mock_state.outcome_state.task_outcome = "SUCCESS"
    mock_state.outcome_state.outcome_reason = "clean run"

    record = TrajectoryRecord.from_final_state(mock_state, "sess-001")
    assert len(record.steps) == 1
    assert record.steps[0].eval_score == 0.9
    assert record.steps[0].eval_failure_type == ""


def test_trajectory_early_attempts_have_default_eval_score() -> None:
    from reforge.runtime.infrastructure.trajectory.models import TrajectoryRecord

    mock_state = MagicMock()
    mock_state.attempts = [
        AttemptRecord(attempt=0, exit_code=1, duration_ms=100, error_type="KeyError"),
        AttemptRecord(attempt=1, exit_code=0, duration_ms=90, error_type=""),
    ]
    mock_state.generated_code = "print('hi')"
    mock_state.user_request = "test"
    mock_state.semantic_state.task_intent = "NORMAL_EXECUTION"
    mock_state.semantic_state.reflection_result = None
    mock_state.semantic_state.evaluation_result = EvaluationResult(
        passed=True, score=0.85, checks=[], summary="ok", failure_type=""
    )
    mock_state.outcome_state.task_outcome = "RECOVERED"
    mock_state.outcome_state.outcome_reason = "retry succeeded"

    record = TrajectoryRecord.from_final_state(mock_state, "sess-002")
    assert record.steps[0].eval_score == 1.0    # early attempt — default
    assert record.steps[1].eval_score == 0.85   # last attempt — real score
