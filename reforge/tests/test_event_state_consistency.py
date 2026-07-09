"""P27 — Event-State Consistency Validator.

Tests cover:
  1. FieldMismatch / ConsistencyReport — construction, immutability, helpers
  2. check_state_consistency — consistent cases (all mapped fields agree)
  3. check_state_consistency — mismatch cases (each field independently)
  4. Edge cases: None fields, no evaluation, float tolerance, enum handling
"""

from __future__ import annotations

import pytest

from reforge.tests._consistency import (
    ConsistencyReport,
    FieldMismatch,
    check_state_consistency,
)
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import (
    evaluation_completed,
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    recovery_attempted,
    reflection_generated,
)
from reforge.runtime.events.projection import project_state
from reforge.runtime.domain.state.models import (
    AttemptRecord,
    EvaluationResult,
    ExecutionState,
    RuntimeControlState,
    RuntimeState,
    SemanticState,
)


# ---------------------------------------------------------------------------
# Helpers — build matching pairs
# ---------------------------------------------------------------------------


def _make_log(**kwargs) -> ExecutionEventLog:
    """Build a log with a single clean success sequence."""
    sid = kwargs.get("sid", "s1")
    log = ExecutionEventLog()
    retry_count = kwargs.get("retry_count", 0)
    policy = kwargs.get("policy", "ACCEPT")

    log.append(execution_started(sid, "t"))
    log.append(execution_succeeded(sid, "t"))
    log.append(evaluation_completed(sid, score=kwargs.get("score", 1.0),
                                    passed=kwargs.get("passed", True)))
    log.append(reflection_generated(sid, kwargs.get("reflection", "")))
    log.append(policy_decided(sid, policy, "reason"))
    for i in range(1, retry_count + 1):
        log.append(recovery_attempted(sid, "t", strategy="llm_retry", attempt=i))
    return log


def _make_state(
    retry_count: int = 0,
    retry_decision_action: str | None = None,
    eval_score: float | None = None,   # None → no EvaluationResult (pre-evaluation)
    eval_passed: bool | None = None,
    reflection: str | None = None,
    exit_code: int | None = None,
    num_attempts: int = 0,
) -> RuntimeState:
    eval_result = (
        EvaluationResult(score=eval_score, passed=bool(eval_passed))
        if eval_score is not None
        else None
    )
    return RuntimeState(
        control_state=RuntimeControlState(
            retry_count=retry_count,
            retry_decision_action=retry_decision_action,
        ),
        semantic_state=SemanticState(
            reflection_summary=reflection,
            evaluation_result=eval_result,
        ),
        exec_state=ExecutionState(exit_code=exit_code),
        attempts=[AttemptRecord(attempt=i + 1) for i in range(num_attempts)],
    )


# ---------------------------------------------------------------------------
# 1. Model construction
# ---------------------------------------------------------------------------


class TestFieldMismatch:
    def test_fields_accessible(self) -> None:
        m = FieldMismatch("retry_count", 2, 1)
        assert m.field_name == "retry_count"
        assert m.projected_value == 2
        assert m.state_value == 1

    def test_immutable(self) -> None:
        m = FieldMismatch("f", 1, 2)
        with pytest.raises((AttributeError, TypeError)):
            m.field_name = "other"  # type: ignore[misc]


class TestConsistencyReport:
    def test_no_mismatches_is_consistent(self) -> None:
        r = ConsistencyReport(session_id="s1", mismatches=())
        assert r.is_consistent is True
        assert r.mismatch_fields() == []

    def test_with_mismatches_not_consistent(self) -> None:
        r = ConsistencyReport(
            session_id="s1",
            mismatches=(FieldMismatch("retry_count", 2, 1),),
        )
        assert r.is_consistent is False
        assert r.mismatch_fields() == ["retry_count"]

    def test_multiple_mismatch_fields(self) -> None:
        r = ConsistencyReport(
            session_id="s1",
            mismatches=(
                FieldMismatch("retry_count", 2, 1),
                FieldMismatch("last_eval_score", 0.5, 0.8),
            ),
        )
        assert set(r.mismatch_fields()) == {"retry_count", "last_eval_score"}

    def test_immutable(self) -> None:
        r = ConsistencyReport("s1", ())
        with pytest.raises((AttributeError, TypeError)):
            r.session_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Consistent cases
# ---------------------------------------------------------------------------


class TestConsistencyConsistent:
    def test_empty_state_and_log(self) -> None:
        log = ExecutionEventLog()
        proj = project_state("s1", log)
        state = RuntimeState()
        report = check_state_consistency(proj, state)
        assert report.is_consistent

    def test_retry_count_zero(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_succeeded("s1", "t"))
        log.append(policy_decided("s1", "ACCEPT", "ok"))
        proj = project_state("s1", log)
        state = _make_state(retry_count=0, retry_decision_action="ACCEPT",
                            exit_code=0, num_attempts=1, eval_score=None)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_retry_count_matches(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_failed("s1", "t", category="syntax", recoverable=True, error="e"))
        log.append(policy_decided("s1", "RETRY", "eval failed"))
        log.append(recovery_attempted("s1", "t", strategy="llm_retry", attempt=1))
        log.append(execution_started("s1", "t"))
        log.append(execution_succeeded("s1", "t"))
        log.append(evaluation_completed("s1", score=1.0, passed=True))
        log.append(policy_decided("s1", "ACCEPT", "ok"))
        proj = project_state("s1", log)
        state = _make_state(retry_count=1, retry_decision_action="ACCEPT",
                            eval_score=1.0, eval_passed=True,
                            exit_code=0, num_attempts=2)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_eval_fields_match(self) -> None:
        log = ExecutionEventLog()
        log.append(evaluation_completed("s1", score=0.75, passed=False))
        proj = project_state("s1", log)
        state = _make_state(eval_score=0.75, eval_passed=False)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_reflection_matches(self) -> None:
        log = ExecutionEventLog()
        log.append(reflection_generated("s1", "missing import"))
        proj = project_state("s1", log)
        state = _make_state(reflection="missing import")
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_none_reflection_matches_empty(self) -> None:
        log = ExecutionEventLog()
        proj = project_state("s1", log)
        state = _make_state(reflection=None)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_execution_succeeded_matches(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_succeeded("s1", "t"))
        proj = project_state("s1", log)
        state = _make_state(exit_code=0, num_attempts=1)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_execution_failed_matches(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_failed("s1", "t", category="runtime_error",
                                    recoverable=True, error="err"))
        proj = project_state("s1", log)
        state = _make_state(exit_code=1, num_attempts=1)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_no_execution_matches_empty_outcome(self) -> None:
        log = ExecutionEventLog()
        proj = project_state("s1", log)
        state = _make_state()
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_current_attempt_matches_attempts_list(self) -> None:
        log = ExecutionEventLog()
        for _ in range(3):
            log.append(execution_started("s1", "t"))
        proj = project_state("s1", log)
        state = _make_state(num_attempts=3)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, report.mismatch_fields()

    def test_eval_skipped_when_state_eval_none(self) -> None:
        log = ExecutionEventLog()
        log.append(evaluation_completed("s1", score=0.5, passed=False))
        proj = project_state("s1", log)
        state = RuntimeState(
            exec_state=ExecutionState(exit_code=None),
        )
        # semantic_state.evaluation_result is None → eval fields not checked → no mismatch on eval
        report = check_state_consistency(proj, state)
        # Only potential mismatch: last_eval_score (0.5 vs N/A) is skipped
        # Other fields still checked
        eval_fields = {"last_eval_score", "last_eval_passed"}
        assert eval_fields.isdisjoint(set(report.mismatch_fields()))


# ---------------------------------------------------------------------------
# 3. Mismatch cases — each field independently
# ---------------------------------------------------------------------------


class TestConsistencyMismatch:
    def test_retry_count_mismatch(self) -> None:
        log = ExecutionEventLog()
        proj = project_state("s1", log)  # retry_count=0
        state = _make_state(retry_count=2, retry_decision_action=None,
                            exit_code=None, num_attempts=0)
        report = check_state_consistency(proj, state)
        assert not report.is_consistent
        assert "retry_count" in report.mismatch_fields()

    def test_policy_decision_mismatch(self) -> None:
        log = ExecutionEventLog()
        log.append(policy_decided("s1", "ACCEPT", "ok"))
        proj = project_state("s1", log)  # last_policy_decision="ACCEPT"
        state = _make_state(retry_decision_action="STOP",
                            exit_code=None, num_attempts=0)
        report = check_state_consistency(proj, state)
        assert not report.is_consistent
        assert "last_policy_decision" in report.mismatch_fields()

    def test_eval_score_mismatch(self) -> None:
        log = ExecutionEventLog()
        log.append(evaluation_completed("s1", score=0.8, passed=True))
        proj = project_state("s1", log)  # last_eval_score=0.8
        state = _make_state(eval_score=0.5, eval_passed=True,
                            retry_decision_action=None, exit_code=None, num_attempts=0)
        report = check_state_consistency(proj, state)
        assert not report.is_consistent
        assert "last_eval_score" in report.mismatch_fields()

    def test_eval_passed_mismatch(self) -> None:
        log = ExecutionEventLog()
        log.append(evaluation_completed("s1", score=1.0, passed=True))
        proj = project_state("s1", log)
        state = _make_state(eval_score=1.0, eval_passed=False,  # disagrees on passed
                            retry_decision_action=None, exit_code=None, num_attempts=0)
        report = check_state_consistency(proj, state)
        assert not report.is_consistent
        assert "last_eval_passed" in report.mismatch_fields()

    def test_reflection_mismatch(self) -> None:
        log = ExecutionEventLog()
        log.append(reflection_generated("s1", "root cause A"))
        proj = project_state("s1", log)
        state = _make_state(reflection="root cause B",
                            retry_decision_action=None, exit_code=None, num_attempts=0)
        report = check_state_consistency(proj, state)
        assert not report.is_consistent
        assert "last_reflection" in report.mismatch_fields()

    def test_execution_outcome_mismatch(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_succeeded("s1", "t"))  # projection says "succeeded"
        proj = project_state("s1", log)
        state = _make_state(exit_code=1, retry_decision_action=None, num_attempts=1)  # state says "failed"
        report = check_state_consistency(proj, state)
        assert not report.is_consistent
        assert "last_execution_outcome" in report.mismatch_fields()

    def test_current_attempt_mismatch(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_started("s1", "t"))  # projection says 2
        proj = project_state("s1", log)
        state = _make_state(num_attempts=1, exit_code=None,
                            retry_decision_action=None)  # state says 1
        report = check_state_consistency(proj, state)
        assert not report.is_consistent
        assert "current_attempt" in report.mismatch_fields()

    def test_multiple_mismatches_all_reported(self) -> None:
        log = ExecutionEventLog()
        log.append(policy_decided("s1", "ACCEPT", "ok"))
        log.append(evaluation_completed("s1", score=0.9, passed=True))
        proj = project_state("s1", log)
        state = _make_state(
            retry_decision_action="STOP",  # mismatch 1
            eval_score=0.3,               # mismatch 2
            eval_passed=True,
            retry_count=0,
            exit_code=None,
            num_attempts=0,
        )
        report = check_state_consistency(proj, state)
        assert not report.is_consistent
        fields = set(report.mismatch_fields())
        assert "last_policy_decision" in fields
        assert "last_eval_score" in fields


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------


class TestConsistencyEdgeCases:
    def test_float_close_enough_is_consistent(self) -> None:
        log = ExecutionEventLog()
        log.append(evaluation_completed("s1", score=0.800000001, passed=True))
        proj = project_state("s1", log)
        state = _make_state(eval_score=0.8, eval_passed=True,
                            retry_decision_action=None, exit_code=None, num_attempts=0)
        report = check_state_consistency(proj, state)
        assert "last_eval_score" not in report.mismatch_fields()

    def test_policy_none_matches_empty_string(self) -> None:
        log = ExecutionEventLog()
        proj = project_state("s1", log)  # last_policy_decision=""
        state = _make_state(retry_decision_action=None, exit_code=None, num_attempts=0)
        report = check_state_consistency(proj, state)
        assert "last_policy_decision" not in report.mismatch_fields()

    def test_report_session_id_preserved(self) -> None:
        log = ExecutionEventLog()
        proj = project_state("my-session", log)
        state = RuntimeState()
        report = check_state_consistency(proj, state)
        assert report.session_id == "my-session"
