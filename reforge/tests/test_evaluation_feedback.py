"""Tests for EvaluationFeedback — check-to-instruction mapping and format_eval_feedback."""

from __future__ import annotations


from reforge.runtime.orchestration.evaluation.feedback import _CHECK_TO_INSTRUCTION, format_eval_feedback
from reforge.runtime.domain.state.models import EvalCheck, EvaluationResult


def _make_result(failed_checks: list[str], passed: bool = False) -> EvaluationResult:
    checks = [
        EvalCheck(name=name, passed=False, detail=f"{name} failed")
        for name in failed_checks
    ]
    return EvaluationResult(
        passed=passed,
        score=0.0 if failed_checks else 1.0,
        checks=checks,
        summary="test",
        failure_type=failed_checks[0] if failed_checks else "",
    )


def test_format_eval_feedback_returns_empty_when_passed() -> None:
    er = EvaluationResult(passed=True, score=1.0, checks=[], summary="ok", failure_type="")
    assert format_eval_feedback(er) == ""


def test_format_eval_feedback_includes_check_name() -> None:
    er = _make_result(["output_not_empty"])
    result = format_eval_feedback(er)
    assert "[output_not_empty]" in result
    assert "print()" in result.lower()


def test_format_eval_feedback_maps_all_known_checks() -> None:
    """Every check in _CHECK_TO_INSTRUCTION should produce a non-empty instruction."""
    for check_name in _CHECK_TO_INSTRUCTION:
        er = _make_result([check_name])
        result = format_eval_feedback(er)
        assert check_name in result
        assert len(result) > 50


def test_format_eval_feedback_multiple_failures() -> None:
    er = _make_result(["output_not_empty", "retry_drift"])
    result = format_eval_feedback(er)
    assert "[output_not_empty]" in result
    assert "[retry_drift]" in result


def test_format_eval_feedback_unknown_check_falls_back_to_detail() -> None:
    check = EvalCheck(name="unknown_custom_check", passed=False, detail="custom detail text")
    er = EvaluationResult(passed=False, score=0.0, checks=[check], summary="x", failure_type="x")
    result = format_eval_feedback(er)
    assert "custom detail text" in result


def test_retry_drift_instruction_mentions_different_approach() -> None:
    er = _make_result(["retry_drift"])
    result = format_eval_feedback(er)
    assert "different" in result.lower()


def test_blanket_except_instruction_mentions_silent_suppression() -> None:
    er = _make_result(["blanket_except_detected"])
    result = format_eval_feedback(er)
    assert "pass" in result.lower() or "suppression" in result.lower()


def test_suspicious_result_instruction_mentions_logic_error() -> None:
    er = _make_result(["suspicious_result"])
    result = format_eval_feedback(er)
    assert "0" in result or "none" in result.lower() or "nan" in result.lower()
