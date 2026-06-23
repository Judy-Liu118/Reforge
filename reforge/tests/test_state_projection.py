"""P26 — Runtime State Projection: derive live state from ExecutionEventLog.

Tests cover:
  1. RuntimeStateProjection — field access, immutability, default values
  2. project_state() — per-kind event accumulation, latest-wins semantics,
     retry sequences, terminal detection, outcome derivation
  3. Session isolation, unknown sessions, multi-event sequences
"""

from __future__ import annotations

import pytest

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
from reforge.runtime.events.projection import RuntimeStateProjection, project_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log(*events) -> ExecutionEventLog:
    log = ExecutionEventLog()
    for e in events:
        log.append(e)
    return log


def _proj(session_id: str, *events) -> RuntimeStateProjection:
    return project_state(session_id, _log(*events))


SID = "test-session"


# ---------------------------------------------------------------------------
# 1. RuntimeStateProjection model
# ---------------------------------------------------------------------------


class TestRuntimeStateProjectionModel:
    def test_fields_accessible(self) -> None:
        p = RuntimeStateProjection(
            session_id="s1",
            retry_count=2,
            current_attempt=3,
            last_execution_outcome="succeeded",
            last_failure_category="syntax",
            last_failure_semantic="syntax_error",
            last_eval_score=0.9,
            last_eval_passed=True,
            last_reflection="fixed colon",
            last_policy_decision="ACCEPT",
            is_terminal=True,
            outcome="succeeded",
            task_completed_outcome="SUCCESS",
        )
        assert p.session_id == "s1"
        assert p.retry_count == 2
        assert p.current_attempt == 3
        assert p.last_execution_outcome == "succeeded"
        assert p.last_failure_category == "syntax"
        assert p.last_failure_semantic == "syntax_error"
        assert p.last_eval_score == pytest.approx(0.9)
        assert p.last_eval_passed is True
        assert p.last_reflection == "fixed colon"
        assert p.last_policy_decision == "ACCEPT"
        assert p.is_terminal is True
        assert p.outcome == "succeeded"

    def test_immutable(self) -> None:
        p = RuntimeStateProjection(
            session_id="s1", retry_count=0, current_attempt=0,
            last_execution_outcome="", last_failure_category="",
            last_failure_semantic="", last_eval_score=0.0, last_eval_passed=False,
            last_reflection="", last_policy_decision="",
            is_terminal=False, outcome="in_progress",
            task_completed_outcome="",
        )
        with pytest.raises((AttributeError, TypeError)):
            p.retry_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. project_state() — empty / no events
# ---------------------------------------------------------------------------


class TestProjectStateEmpty:
    def test_empty_log_all_defaults(self) -> None:
        p = project_state(SID, ExecutionEventLog())
        assert p.session_id == SID
        assert p.retry_count == 0
        assert p.current_attempt == 0
        assert p.last_execution_outcome == ""
        assert p.last_failure_category == ""
        assert p.last_failure_semantic == ""
        assert p.last_eval_score == pytest.approx(0.0)
        assert p.last_eval_passed is False
        assert p.last_reflection == ""
        assert p.last_policy_decision == ""
        assert p.is_terminal is False
        assert p.outcome == "in_progress"

    def test_unknown_session_returns_defaults(self) -> None:
        log = _log(execution_started("other", "t"))
        p = project_state("ghost", log)
        assert p.current_attempt == 0
        assert p.outcome == "in_progress"

    def test_initial_is_terminal_false(self) -> None:
        p = project_state(SID, ExecutionEventLog())
        assert p.is_terminal is False


# ---------------------------------------------------------------------------
# 3. Individual event kinds
# ---------------------------------------------------------------------------


class TestProjectStatePerKind:
    def test_execution_started_increments_attempt(self) -> None:
        p = _proj(SID, execution_started(SID, "t"))
        assert p.current_attempt == 1

    def test_multiple_starts_count_all(self) -> None:
        events = [execution_started(SID, "t")] * 3
        p = project_state(SID, _log(*events))
        assert p.current_attempt == 3

    def test_execution_succeeded_sets_outcome(self) -> None:
        p = _proj(SID, execution_started(SID, "t"), execution_succeeded(SID, "t"))
        assert p.last_execution_outcome == "succeeded"

    def test_execution_failed_sets_outcome(self) -> None:
        p = _proj(SID, execution_started(SID, "t"),
                  execution_failed(SID, "t", category="runtime_error", recoverable=True, error="err"))
        assert p.last_execution_outcome == "failed"

    def test_execution_failed_extracts_category(self) -> None:
        p = _proj(SID, execution_failed(SID, "t", category="syntax",
                                        recoverable=True, error="SyntaxError"))
        assert p.last_failure_category == "syntax"

    def test_execution_failed_extracts_semantic(self) -> None:
        p = _proj(SID, execution_failed(SID, "t", category="dependency",
                                        recoverable=True, error="ImportError",
                                        semantic_meaning="missing_package"))
        assert p.last_failure_semantic == "missing_package"

    def test_execution_failed_empty_semantic_by_default(self) -> None:
        p = _proj(SID, execution_failed(SID, "t", category="runtime_error",
                                        recoverable=False, error="err"))
        assert p.last_failure_semantic == ""

    def test_evaluation_score_extracted(self) -> None:
        p = _proj(SID, evaluation_completed(SID, score=0.75, passed=False))
        assert p.last_eval_score == pytest.approx(0.75)

    def test_evaluation_passed_extracted(self) -> None:
        p = _proj(SID, evaluation_completed(SID, score=1.0, passed=True))
        assert p.last_eval_passed is True

    def test_reflection_extracted(self) -> None:
        p = _proj(SID, reflection_generated(SID, "root cause: missing import"))
        assert p.last_reflection == "root cause: missing import"

    def test_recovery_increments_retry_count(self) -> None:
        p = _proj(SID, recovery_attempted(SID, "t", strategy="llm_retry", attempt=1))
        assert p.retry_count == 1

    def test_multiple_recoveries_count(self) -> None:
        events = [
            recovery_attempted(SID, "t", strategy="llm_retry", attempt=i)
            for i in range(1, 4)
        ]
        p = project_state(SID, _log(*events))
        assert p.retry_count == 3


# ---------------------------------------------------------------------------
# 4. Policy decisions and terminal detection
# ---------------------------------------------------------------------------


class TestPolicyAndTerminal:
    def test_policy_accept_is_terminal(self) -> None:
        p = _proj(SID, policy_decided(SID, "ACCEPT", "clean run"))
        assert p.is_terminal is True
        assert p.outcome == "succeeded"
        assert p.last_policy_decision == "ACCEPT"

    def test_policy_stop_is_terminal(self) -> None:
        p = _proj(SID, policy_decided(SID, "STOP", "max retries"))
        assert p.is_terminal is True
        assert p.outcome == "failed"

    def test_policy_retry_not_terminal(self) -> None:
        p = _proj(SID, policy_decided(SID, "RETRY", "eval failed"))
        assert p.is_terminal is False
        assert p.outcome == "in_progress"

    def test_outcome_in_progress_before_policy(self) -> None:
        p = _proj(SID, execution_started(SID, "t"), execution_succeeded(SID, "t"),
                  evaluation_completed(SID, score=1.0, passed=True))
        assert p.outcome == "in_progress"

    def test_outcome_succeeded_after_accept(self) -> None:
        p = _proj(SID,
                  execution_started(SID, "t"),
                  execution_succeeded(SID, "t"),
                  evaluation_completed(SID, score=1.0, passed=True),
                  policy_decided(SID, "ACCEPT", "clean"))
        assert p.outcome == "succeeded"
        assert p.is_terminal is True

    def test_outcome_failed_after_stop(self) -> None:
        p = _proj(SID,
                  execution_started(SID, "t"),
                  execution_failed(SID, "t", category="runtime_error", recoverable=False, error="e"),
                  evaluation_completed(SID, score=0.0, passed=False),
                  policy_decided(SID, "STOP", "give up"))
        assert p.outcome == "failed"
        assert p.is_terminal is True


# ---------------------------------------------------------------------------
# 5. Latest-wins semantics
# ---------------------------------------------------------------------------


class TestLatestWins:
    def test_latest_eval_supersedes_earlier(self) -> None:
        p = _proj(SID,
                  evaluation_completed(SID, score=0.2, passed=False),
                  evaluation_completed(SID, score=0.95, passed=True))
        assert p.last_eval_score == pytest.approx(0.95)
        assert p.last_eval_passed is True

    def test_latest_reflection_supersedes_earlier(self) -> None:
        p = _proj(SID,
                  reflection_generated(SID, "first reflection"),
                  reflection_generated(SID, "second reflection"))
        assert p.last_reflection == "second reflection"

    def test_latest_policy_supersedes_earlier(self) -> None:
        p = _proj(SID,
                  policy_decided(SID, "RETRY", "round 1"),
                  policy_decided(SID, "ACCEPT", "round 2"))
        assert p.last_policy_decision == "ACCEPT"
        assert p.is_terminal is True
        assert p.outcome == "succeeded"

    def test_failure_category_stale_after_succeed(self) -> None:
        # failure category not cleared by a subsequent succeed event
        p = _proj(SID,
                  execution_failed(SID, "t", category="syntax", recoverable=True, error="e"),
                  execution_succeeded(SID, "t"))
        assert p.last_execution_outcome == "succeeded"
        assert p.last_failure_category == "syntax"  # stale, but not overwritten


# ---------------------------------------------------------------------------
# 6. Retry sequences
# ---------------------------------------------------------------------------


class TestRetrySequences:
    def test_retry_sequence_fields(self) -> None:
        p = _proj(SID,
                  # Attempt 1
                  execution_started(SID, "t"),
                  execution_failed(SID, "t", category="syntax", recoverable=True, error="e"),
                  evaluation_completed(SID, score=0.3, passed=False),
                  reflection_generated(SID, "missing colon"),
                  policy_decided(SID, "RETRY", "eval failed"),
                  recovery_attempted(SID, "t", strategy="llm_retry", attempt=1),
                  # Attempt 2
                  execution_started(SID, "t"),
                  execution_succeeded(SID, "t"),
                  evaluation_completed(SID, score=1.0, passed=True),
                  policy_decided(SID, "ACCEPT", "clean"))
        assert p.retry_count == 1
        assert p.current_attempt == 2
        assert p.last_execution_outcome == "succeeded"
        assert p.last_eval_score == pytest.approx(1.0)
        assert p.last_eval_passed is True
        assert p.last_policy_decision == "ACCEPT"
        assert p.outcome == "succeeded"
        assert p.is_terminal is True

    def test_multiple_retries_then_stop(self) -> None:
        log = ExecutionEventLog()
        for i in range(1, 3):
            log.append(execution_started(SID, "t"))
            log.append(execution_failed(SID, "t", category="runtime_error", recoverable=True, error="err"))
            log.append(evaluation_completed(SID, score=0.1, passed=False))
            log.append(policy_decided(SID, "RETRY", "eval failed"))
            log.append(recovery_attempted(SID, "t", strategy="llm_retry", attempt=i))
        log.append(execution_started(SID, "t"))
        log.append(execution_failed(SID, "t", category="runtime_error", recoverable=False, error="err"))
        log.append(evaluation_completed(SID, score=0.0, passed=False))
        log.append(policy_decided(SID, "STOP", "max retries"))

        p = project_state(SID, log)
        assert p.retry_count == 2
        assert p.current_attempt == 3
        assert p.outcome == "failed"
        assert p.is_terminal is True


# ---------------------------------------------------------------------------
# 7. Session isolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    def test_multi_session_independent(self) -> None:
        log = ExecutionEventLog()
        # alice succeeds
        log.append(execution_started("alice", "t"))
        log.append(execution_succeeded("alice", "t"))
        log.append(policy_decided("alice", "ACCEPT", "ok"))
        # bob fails
        log.append(execution_started("bob", "t"))
        log.append(execution_failed("bob", "t", category="syntax", recoverable=False, error="e"))
        log.append(policy_decided("bob", "STOP", "err"))

        alice = project_state("alice", log)
        bob = project_state("bob", log)

        assert alice.outcome == "succeeded"
        assert alice.last_execution_outcome == "succeeded"
        assert alice.retry_count == 0

        assert bob.outcome == "failed"
        assert bob.last_execution_outcome == "failed"
        assert bob.last_failure_category == "syntax"

    def test_retry_count_not_shared(self) -> None:
        log = ExecutionEventLog()
        for i in range(1, 4):
            log.append(recovery_attempted("alice", "t", strategy="llm_retry", attempt=i))
        log.append(recovery_attempted("bob", "t", strategy="llm_retry", attempt=1))

        assert project_state("alice", log).retry_count == 3
        assert project_state("bob", log).retry_count == 1
