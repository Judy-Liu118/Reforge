"""P25 — Session Replay: projection of ExecutionEventLog into session summaries.

Tests cover:
  1. AttemptSummary / SessionSummary — field construction and access
  2. _build_summary / SessionReplay.summarize() — single success, single failure,
     retry-then-success, max-retries failure, partial session, multi-session,
     field extraction (category / semantic / eval / reflection / policy)
  3. SessionReplay.all_summaries() — ordering, isolation
  4. render_summary / SessionReplay.render() — output contains key fields
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
from reforge.runtime.events.replay import (
    AttemptSummary,
    SessionReplay,
    SessionSummary,
    render_summary,
)


# ---------------------------------------------------------------------------
# Helpers — build a log programmatically
# ---------------------------------------------------------------------------


def _success_sequence(log: ExecutionEventLog, session_id: str, task: str = "t") -> None:
    """One clean successful attempt."""
    log.append(execution_started(session_id, task))
    log.append(execution_succeeded(session_id, task))
    log.append(evaluation_completed(session_id, score=1.0, passed=True))
    log.append(policy_decided(session_id, "ACCEPT", "clean execution"))


def _failure_sequence(
    log: ExecutionEventLog,
    session_id: str,
    category: str = "runtime_error",
    task: str = "t",
) -> None:
    """One failed attempt ending in STOP."""
    log.append(execution_started(session_id, task))
    log.append(
        execution_failed(
            session_id, task,
            category=category,  # type: ignore[arg-type]
            recoverable=False,
            error="something went wrong",
            semantic_meaning="missing_package" if category == "dependency" else "",
        )
    )
    log.append(evaluation_completed(session_id, score=0.2, passed=False, reasons=["eval_fail"]))
    log.append(reflection_generated(session_id, "root cause: import error"))
    log.append(policy_decided(session_id, "STOP", "max retries"))


def _retry_sequence(
    log: ExecutionEventLog,
    session_id: str,
    task: str = "t",
) -> None:
    """Two attempts: first fails (RETRY), second succeeds (ACCEPT)."""
    # Attempt 1 — failure → RETRY
    log.append(execution_started(session_id, task))
    log.append(
        execution_failed(
            session_id, task,
            category="syntax",
            recoverable=True,
            error="SyntaxError: invalid syntax",
            semantic_meaning="syntax_error",
        )
    )
    log.append(evaluation_completed(session_id, score=0.3, passed=False))
    log.append(reflection_generated(session_id, "missing colon in function def"))
    log.append(policy_decided(session_id, "RETRY", "eval failed"))
    log.append(recovery_attempted(session_id, task, strategy="llm_retry", attempt=1))

    # Attempt 2 — success → ACCEPT
    log.append(execution_started(session_id, task))
    log.append(execution_succeeded(session_id, task))
    log.append(evaluation_completed(session_id, score=1.0, passed=True))
    log.append(policy_decided(session_id, "ACCEPT", "clean execution"))


# ---------------------------------------------------------------------------
# 1. Model construction
# ---------------------------------------------------------------------------


class TestAttemptSummary:
    def test_fields_accessible(self) -> None:
        a = AttemptSummary(
            attempt_number=1,
            execution_outcome="succeeded",
            failure_category="",
            semantic_meaning="",
            error_summary="",
            eval_score=1.0,
            eval_passed=True,
            reflection_summary="",
            policy_decision="ACCEPT",
        )
        assert a.attempt_number == 1
        assert a.execution_outcome == "succeeded"
        assert a.eval_score == pytest.approx(1.0)
        assert a.eval_passed is True
        assert a.policy_decision == "ACCEPT"

    def test_immutable(self) -> None:
        a = AttemptSummary(
            attempt_number=1, execution_outcome="succeeded",
            failure_category="", semantic_meaning="", error_summary="",
            eval_score=1.0, eval_passed=True, reflection_summary="",
            policy_decision="ACCEPT",
        )
        with pytest.raises((AttributeError, TypeError)):
            a.attempt_number = 99  # type: ignore[misc]


class TestSessionSummary:
    def test_fields_accessible(self) -> None:
        s = SessionSummary(
            session_id="s1", total_attempts=2,
            final_outcome="succeeded", recovery_count=1,
            attempts=(),
        )
        assert s.session_id == "s1"
        assert s.total_attempts == 2
        assert s.final_outcome == "succeeded"
        assert s.recovery_count == 1

    def test_immutable(self) -> None:
        s = SessionSummary("s1", 1, "succeeded", 0, ())
        with pytest.raises((AttributeError, TypeError)):
            s.session_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. SessionReplay.summarize()
# ---------------------------------------------------------------------------


class TestSessionReplaySummarize:
    def test_empty_log_returns_in_progress(self) -> None:
        log = ExecutionEventLog()
        replay = SessionReplay(log)
        s = replay.summarize("unknown-session")
        assert s.session_id == "unknown-session"
        assert s.total_attempts == 0
        assert s.final_outcome == "in_progress"
        assert s.recovery_count == 0

    def test_single_success(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "s1")
        s = SessionReplay(log).summarize("s1")
        assert s.total_attempts == 1
        assert s.final_outcome == "succeeded"
        assert s.recovery_count == 0

    def test_single_success_attempt_fields(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "s1")
        a = SessionReplay(log).summarize("s1").attempts[0]
        assert a.attempt_number == 1
        assert a.execution_outcome == "succeeded"
        assert a.eval_score == pytest.approx(1.0)
        assert a.eval_passed is True
        assert a.policy_decision == "ACCEPT"

    def test_single_failure_stop(self) -> None:
        log = ExecutionEventLog()
        _failure_sequence(log, "s1")
        s = SessionReplay(log).summarize("s1")
        assert s.total_attempts == 1
        assert s.final_outcome == "failed"

    def test_failure_category_extracted(self) -> None:
        log = ExecutionEventLog()
        _failure_sequence(log, "s1", category="dependency")
        a = SessionReplay(log).summarize("s1").attempts[0]
        assert a.failure_category == "dependency"
        assert a.semantic_meaning == "missing_package"

    def test_failure_eval_extracted(self) -> None:
        log = ExecutionEventLog()
        _failure_sequence(log, "s1")
        a = SessionReplay(log).summarize("s1").attempts[0]
        assert a.eval_score == pytest.approx(0.2)
        assert a.eval_passed is False

    def test_failure_reflection_extracted(self) -> None:
        log = ExecutionEventLog()
        _failure_sequence(log, "s1")
        a = SessionReplay(log).summarize("s1").attempts[0]
        assert "import error" in a.reflection_summary

    def test_failure_policy_decision_stop(self) -> None:
        log = ExecutionEventLog()
        _failure_sequence(log, "s1")
        a = SessionReplay(log).summarize("s1").attempts[0]
        assert a.policy_decision == "STOP"

    def test_retry_then_success(self) -> None:
        log = ExecutionEventLog()
        _retry_sequence(log, "s1")
        s = SessionReplay(log).summarize("s1")
        assert s.total_attempts == 2
        assert s.final_outcome == "succeeded"
        assert s.recovery_count == 1

    def test_retry_attempt_1_is_failure(self) -> None:
        log = ExecutionEventLog()
        _retry_sequence(log, "s1")
        a1 = SessionReplay(log).summarize("s1").attempts[0]
        assert a1.execution_outcome == "failed"
        assert a1.policy_decision == "RETRY"
        assert a1.failure_category == "syntax"

    def test_retry_attempt_2_is_success(self) -> None:
        log = ExecutionEventLog()
        _retry_sequence(log, "s1")
        a2 = SessionReplay(log).summarize("s1").attempts[1]
        assert a2.execution_outcome == "succeeded"
        assert a2.policy_decision == "ACCEPT"

    def test_multiple_retries_then_stop(self) -> None:
        log = ExecutionEventLog()
        sid = "multi-retry"
        for i in range(1, 3):
            log.append(execution_started(sid, "t"))
            log.append(execution_failed(sid, "t", category="runtime_error", recoverable=True, error="err"))
            log.append(evaluation_completed(sid, score=0.1, passed=False))
            log.append(policy_decided(sid, "RETRY", "eval failed"))
            log.append(recovery_attempted(sid, "t", strategy="llm_retry", attempt=i))
        # Final attempt
        log.append(execution_started(sid, "t"))
        log.append(execution_failed(sid, "t", category="runtime_error", recoverable=False, error="err"))
        log.append(evaluation_completed(sid, score=0.0, passed=False))
        log.append(policy_decided(sid, "STOP", "max retries"))

        s = SessionReplay(log).summarize(sid)
        assert s.total_attempts == 3
        assert s.final_outcome == "failed"
        assert s.recovery_count == 2

    def test_partial_session_no_policy_decided(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_failed("s1", "t", category="syntax", recoverable=True, error="err"))
        # No EVALUATION_COMPLETED or POLICY_DECIDED yet
        s = SessionReplay(log).summarize("s1")
        assert s.total_attempts == 1
        assert s.final_outcome == "in_progress"
        assert s.attempts[0].policy_decision == ""

    def test_error_summary_truncated(self) -> None:
        long_error = "x" * 500
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_failed("s1", "t", category="runtime_error", recoverable=True, error=long_error))
        log.append(evaluation_completed("s1", score=0.0, passed=False))
        log.append(policy_decided("s1", "STOP", "err"))
        a = SessionReplay(log).summarize("s1").attempts[0]
        assert len(a.error_summary) <= 120

    def test_sessions_are_independent(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "alice")
        _failure_sequence(log, "bob")
        alice = SessionReplay(log).summarize("alice")
        bob = SessionReplay(log).summarize("bob")
        assert alice.final_outcome == "succeeded"
        assert bob.final_outcome == "failed"
        assert alice.total_attempts == 1
        assert bob.total_attempts == 1

    def test_unknown_session_returns_empty_in_progress(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "s1")
        s = SessionReplay(log).summarize("ghost")
        assert s.total_attempts == 0
        assert s.final_outcome == "in_progress"


# ---------------------------------------------------------------------------
# 3. all_summaries
# ---------------------------------------------------------------------------


class TestAllSummaries:
    def test_returns_summary_per_session(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "a")
        _failure_sequence(log, "b")
        summaries = SessionReplay(log).all_summaries()
        assert len(summaries) == 2

    def test_sorted_by_session_id(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "z-session")
        _success_sequence(log, "a-session")
        summaries = SessionReplay(log).all_summaries()
        assert summaries[0].session_id == "a-session"
        assert summaries[1].session_id == "z-session"

    def test_empty_log_returns_empty_list(self) -> None:
        assert SessionReplay(ExecutionEventLog()).all_summaries() == []


# ---------------------------------------------------------------------------
# 4. render_summary / SessionReplay.render()
# ---------------------------------------------------------------------------


class TestRenderSummary:
    def test_contains_session_id(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "test-sid")
        text = SessionReplay(log).render("test-sid")
        assert "test-sid" in text

    def test_contains_outcome(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "s1")
        assert "succeeded" in SessionReplay(log).render("s1")

    def test_contains_attempt_number(self) -> None:
        log = ExecutionEventLog()
        _retry_sequence(log, "s1")
        text = SessionReplay(log).render("s1")
        assert "Attempt 1" in text
        assert "Attempt 2" in text

    def test_contains_failure_category(self) -> None:
        log = ExecutionEventLog()
        _failure_sequence(log, "s1", category="dependency")
        text = SessionReplay(log).render("s1")
        assert "dependency" in text

    def test_contains_recovery_count(self) -> None:
        log = ExecutionEventLog()
        _retry_sequence(log, "s1")
        text = SessionReplay(log).render("s1")
        assert "Recoveries" in text or "1" in text

    def test_render_summary_standalone(self) -> None:
        summary = SessionSummary(
            session_id="standalone",
            total_attempts=1,
            final_outcome="succeeded",
            recovery_count=0,
            attempts=(
                AttemptSummary(
                    attempt_number=1,
                    execution_outcome="succeeded",
                    failure_category="",
                    semantic_meaning="",
                    error_summary="",
                    eval_score=1.0,
                    eval_passed=True,
                    reflection_summary="",
                    policy_decision="ACCEPT",
                ),
            ),
        )
        text = render_summary(summary)
        assert "standalone" in text
        assert "succeeded" in text
        assert "Attempt 1" in text

    def test_render_reflection_included(self) -> None:
        log = ExecutionEventLog()
        _failure_sequence(log, "s1")
        text = SessionReplay(log).render("s1")
        assert "import error" in text

    def test_render_policy_decision_included(self) -> None:
        log = ExecutionEventLog()
        _success_sequence(log, "s1")
        text = SessionReplay(log).render("s1")
        assert "ACCEPT" in text
