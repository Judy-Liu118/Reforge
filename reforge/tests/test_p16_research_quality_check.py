"""Tests for P16.4 — research_output_quality HeuristicEvaluator check."""

from __future__ import annotations

from reforge.runtime.orchestration.evaluation.heuristics import HeuristicEvaluator
from reforge.runtime.domain.state.models import ExecutionState, RuntimeState


def _make_state(user_request: str, stdout: str, exit_code: int = 0) -> RuntimeState:
    return RuntimeState(
        user_request=user_request,
        exec_state=ExecutionState(stdout=stdout, stderr="", exit_code=exit_code),
    )


class TestResearchOutputQualityCheck:
    def setup_method(self) -> None:
        self.ev = HeuristicEvaluator()

    def test_passes_for_non_research_task(self) -> None:
        state = _make_state("Read CSV and calculate mean", "Result: ok")
        result = self.ev.evaluate(state)
        check_names = [c.name for c in result.checks]
        assert "research_output_quality" not in check_names

    def test_passes_when_output_has_numbers(self) -> None:
        state = _make_state(
            "Verify that the column count is correct",
            "Column count: 42 columns found in dataset",
        )
        result = self.ev.evaluate(state)
        rq = next((c for c in result.checks if c.name == "research_output_quality"), None)
        assert rq is None or rq.passed

    def test_fails_when_output_too_short_no_numbers(self) -> None:
        state = _make_state(
            "Verify the column exists",
            "ok",  # too short, no numbers
        )
        result = self.ev.evaluate(state)
        rq = next((c for c in result.checks if c.name == "research_output_quality"), None)
        assert rq is not None
        assert not rq.passed

    def test_fails_when_output_has_no_numbers(self) -> None:
        state = _make_state(
            "Check if the data is valid",
            "The data appears to be loading correctly",  # long but no numbers
        )
        result = self.ev.evaluate(state)
        rq = next((c for c in result.checks if c.name == "research_output_quality"), None)
        assert rq is not None
        assert not rq.passed

    def test_passes_for_verify_with_numbers(self) -> None:
        state = _make_state(
            "Verify that row count matches expected",
            "Row count: 1000 rows found. Expected: 1000. Match: True",
        )
        result = self.ev.evaluate(state)
        rq = next((c for c in result.checks if c.name == "research_output_quality"), None)
        assert rq is None or rq.passed

    def test_check_whether_triggers(self) -> None:
        state = _make_state(
            "Check whether the index is unique",
            "yes",  # too short, no numbers
        )
        result = self.ev.evaluate(state)
        rq = next((c for c in result.checks if c.name == "research_output_quality"), None)
        assert rq is not None
        assert not rq.passed

    def test_failure_type_is_insufficient_output(self) -> None:
        # stdout long enough to pass output_not_empty but has no digits
        state = _make_state(
            "Verify the column exists in the table",
            "The column appears to be present in the schema",  # >5 chars, no digits
        )
        result = self.ev.evaluate(state)
        assert result.failure_type == "insufficient_output"

    def test_feedback_has_research_quality_instruction(self) -> None:
        from reforge.runtime.orchestration.evaluation.feedback import format_eval_feedback
        from reforge.runtime.domain.state.models import EvalCheck, EvaluationResult

        er = EvaluationResult(
            passed=False,
            score=0.5,
            checks=[EvalCheck(name="research_output_quality", passed=False, detail="too short")],
            summary="failed",
            failure_type="insufficient_output",
        )
        feedback = format_eval_feedback(er)
        assert "quantitative" in feedback.lower()
        assert "research_output_quality" in feedback
