"""P38 — TASK_COMPLETED event: final lifecycle coverage.

Tests cover:
  1. task_completed() factory — payload shape, field values
  2. wrap_final_response_node() — emits TASK_COMPLETED from outcome_state
  3. project_state() — task_completed_outcome field, is_terminal, outcome
  4. consistency.check_state_consistency() — 8th field check
  5. progress.format_live_event() — TASK_COMPLETED display
  6. Backward compatibility — no TASK_COMPLETED → task_completed_outcome == ""
  7. Denied path — DENIED outcome emitted correctly
"""

from __future__ import annotations


import pytest

from reforge.cli.progress import format_live_event
from reforge.tests._consistency import check_state_consistency
from reforge.runtime.events.emitters import wrap_final_response_node
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.models import (
    execution_started,
    execution_succeeded,
    policy_decided,
    task_completed,
)
from reforge.runtime.events.projection import project_state
from reforge.runtime.domain.state.models import OutcomeState, RuntimeState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_final_response_fn(task_outcome: str, reason: str, answer: str = "ok"):
    """Fake final_response_node that returns a known outcome_state."""
    def node(state: RuntimeState) -> dict:
        return {
            "outcome_state": state.outcome_state.model_copy(
                update={
                    "task_outcome": task_outcome,
                    "outcome_reason": reason,
                    "final_answer": answer,
                }
            )
        }
    return node


def _make_final_response_dict_fn(task_outcome: str, reason: str, answer: str = "ok"):
    """Fake node that returns outcome_state as a plain dict."""
    def node(state: RuntimeState) -> dict:
        return {
            "outcome_state": {
                "task_outcome": task_outcome,
                "outcome_reason": reason,
                "final_answer": answer,
            }
        }
    return node


# ---------------------------------------------------------------------------
# 1. task_completed() factory
# ---------------------------------------------------------------------------


class TestTaskCompletedFactory:
    def test_kind_is_task_completed(self) -> None:
        ev = task_completed("s1", "SUCCESS", "clean")
        assert ev.kind == "TASK_COMPLETED"

    def test_session_id_stored(self) -> None:
        ev = task_completed("my-session", "SUCCESS", "ok")
        assert ev.session_id == "my-session"

    def test_outcome_in_payload(self) -> None:
        ev = task_completed("s1", "FAILED", "timeout")
        assert ev.payload["outcome"] == "FAILED"

    def test_reason_in_payload(self) -> None:
        ev = task_completed("s1", "SUCCESS", "clean output")
        assert ev.payload["reason"] == "clean output"

    def test_answer_summary_in_payload(self) -> None:
        ev = task_completed("s1", "SUCCESS", "ok", answer_summary="Hello World")
        assert ev.payload["answer_summary"] == "Hello World"

    def test_answer_summary_defaults_to_empty(self) -> None:
        ev = task_completed("s1", "SUCCESS", "ok")
        assert ev.payload["answer_summary"] == ""

    def test_event_is_immutable(self) -> None:
        ev = task_completed("s1", "SUCCESS", "ok")
        with pytest.raises((AttributeError, TypeError)):
            ev.kind = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. wrap_final_response_node()
# ---------------------------------------------------------------------------


class TestWrapFinalResponseNode:
    def test_emits_task_completed_event(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="task")
        wrapped = wrap_final_response_node(
            _make_final_response_fn("SUCCESS", "clean"), log, "s1"
        )
        wrapped(state)
        events = log.query(kind="TASK_COMPLETED", session_id="s1")
        assert len(events) == 1

    def test_outcome_captured_in_event(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="task")
        wrapped = wrap_final_response_node(
            _make_final_response_fn("RECOVERED", "retry worked"), log, "s1"
        )
        wrapped(state)
        ev = log.query(kind="TASK_COMPLETED", session_id="s1")[0]
        assert ev.payload["outcome"] == "RECOVERED"

    def test_reason_captured_in_event(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="task")
        wrapped = wrap_final_response_node(
            _make_final_response_fn("SUCCESS", "score 1.0"), log, "s1"
        )
        wrapped(state)
        ev = log.query(kind="TASK_COMPLETED", session_id="s1")[0]
        assert ev.payload["reason"] == "score 1.0"

    def test_answer_summary_truncated_to_200(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="task")
        long_answer = "x" * 500
        wrapped = wrap_final_response_node(
            _make_final_response_fn("SUCCESS", "ok", long_answer), log, "s1"
        )
        wrapped(state)
        ev = log.query(kind="TASK_COMPLETED", session_id="s1")[0]
        assert len(ev.payload["answer_summary"]) == 200

    def test_dict_outcome_state_also_works(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="task")
        wrapped = wrap_final_response_node(
            _make_final_response_dict_fn("FAILED", "timeout"), log, "s1"
        )
        wrapped(state)
        events = log.query(kind="TASK_COMPLETED", session_id="s1")
        assert len(events) == 1
        assert events[0].payload["outcome"] == "FAILED"

    def test_no_outcome_state_no_event(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="task")

        def no_outcome_state(s: RuntimeState) -> dict:
            return {"user_request": s.user_request}

        wrapped = wrap_final_response_node(no_outcome_state, log, "s1")
        wrapped(state)
        assert len(log.query(kind="TASK_COMPLETED")) == 0

    def test_none_event_log_returns_original_fn(self) -> None:
        original = _make_final_response_fn("SUCCESS", "ok")
        wrapped = wrap_final_response_node(original, None, "s1")
        assert wrapped is original

    def test_result_dict_unchanged(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="task")
        wrapped = wrap_final_response_node(
            _make_final_response_fn("SUCCESS", "ok"), log, "s1"
        )
        result = wrapped(state)
        assert "outcome_state" in result


# ---------------------------------------------------------------------------
# 3. project_state() — task_completed_outcome field
# ---------------------------------------------------------------------------


class TestProjectStateTaskCompleted:
    def test_no_events_task_completed_outcome_empty(self) -> None:
        log = ExecutionEventLog()
        p = project_state("s1", log)
        assert p.task_completed_outcome == ""

    def test_task_completed_event_captured(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "SUCCESS", "ok"))
        p = project_state("s1", log)
        assert p.task_completed_outcome == "SUCCESS"

    def test_task_completed_overrides_outcome_for_success(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        log.append(execution_succeeded("s1", "task"))
        log.append(policy_decided("s1", "ACCEPT", "clean"))
        log.append(task_completed("s1", "SUCCESS", "clean output"))
        p = project_state("s1", log)
        assert p.outcome == "succeeded"

    def test_task_completed_overrides_outcome_for_failed(self) -> None:
        log = ExecutionEventLog()
        log.append(policy_decided("s1", "STOP", "max retries"))
        log.append(task_completed("s1", "FAILED", "gave up"))
        p = project_state("s1", log)
        assert p.outcome == "failed"

    def test_recovered_outcome_maps_to_succeeded(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "RECOVERED", "retry worked"))
        p = project_state("s1", log)
        assert p.outcome == "succeeded"

    def test_expected_failure_maps_to_succeeded(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "EXPECTED_FAILURE", "intentional"))
        p = project_state("s1", log)
        assert p.outcome == "succeeded"

    def test_denied_maps_to_failed(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "DENIED", "policy"))
        p = project_state("s1", log)
        assert p.outcome == "failed"

    def test_is_terminal_true_when_task_completed(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "SUCCESS", "done"))
        p = project_state("s1", log)
        assert p.is_terminal is True

    def test_is_terminal_false_with_no_events(self) -> None:
        log = ExecutionEventLog()
        p = project_state("s1", log)
        assert p.is_terminal is False

    def test_latest_task_completed_wins(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "FAILED", "first"))
        log.append(task_completed("s1", "SUCCESS", "second"))
        p = project_state("s1", log)
        assert p.task_completed_outcome == "SUCCESS"

    def test_other_session_not_affected(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "SUCCESS", "done"))
        p = project_state("s2", log)
        assert p.task_completed_outcome == ""


# ---------------------------------------------------------------------------
# 4. consistency.check_state_consistency() — 8th field
# ---------------------------------------------------------------------------


class TestConsistencyTaskCompleted:
    def test_consistent_when_both_empty(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState()
        p = project_state("s1", log)
        report = check_state_consistency(p, state)
        assert "task_completed_outcome" not in report.mismatch_fields()

    def test_consistent_when_task_outcome_matches(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "SUCCESS", "ok"))
        state = RuntimeState(
            outcome_state=OutcomeState(
                task_outcome="SUCCESS",
                outcome_reason="ok",
                final_answer="done",
            )
        )
        p = project_state("s1", log)
        report = check_state_consistency(p, state)
        assert "task_completed_outcome" not in report.mismatch_fields()

    def test_mismatch_detected(self) -> None:
        log = ExecutionEventLog()
        log.append(task_completed("s1", "SUCCESS", "ok"))
        state = RuntimeState(
            outcome_state=OutcomeState(task_outcome="FAILED", outcome_reason="wrong")
        )
        p = project_state("s1", log)
        report = check_state_consistency(p, state)
        assert "task_completed_outcome" in report.mismatch_fields()

    def test_check_skipped_when_no_task_completed_event(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "task"))
        state = RuntimeState(
            outcome_state=OutcomeState(task_outcome="SUCCESS")
        )
        p = project_state("s1", log)
        # projection.task_completed_outcome is "" → check is skipped
        report = check_state_consistency(p, state)
        assert "task_completed_outcome" not in report.mismatch_fields()


# ---------------------------------------------------------------------------
# 5. progress.format_live_event() — TASK_COMPLETED display
# ---------------------------------------------------------------------------


class TestProgressTaskCompleted:
    def test_task_completed_returns_line(self) -> None:
        ev = task_completed("s1", "SUCCESS", "clean output")
        line = format_live_event(ev)
        assert line is not None

    def test_task_completed_contains_symbol(self) -> None:
        ev = task_completed("s1", "SUCCESS", "clean")
        line = format_live_event(ev)
        assert "◆" in line

    def test_task_completed_includes_outcome(self) -> None:
        ev = task_completed("s1", "RECOVERED", "retry worked")
        line = format_live_event(ev)
        assert "RECOVERED" in line

    def test_task_completed_includes_reason(self) -> None:
        ev = task_completed("s1", "FAILED", "max retries reached")
        line = format_live_event(ev)
        assert "max retries reached" in line

    def test_task_completed_indented(self) -> None:
        ev = task_completed("s1", "SUCCESS", "ok")
        line = format_live_event(ev)
        assert line.startswith("  ")

    def test_task_completed_empty_reason_no_dash(self) -> None:
        ev = task_completed("s1", "DENIED", "")
        line = format_live_event(ev)
        assert "—" not in line


# ---------------------------------------------------------------------------
# 6. wrap_final_response_node + full pipeline integration
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    def test_wrap_final_response_emits_task_completed_and_project_state_consistent(
        self,
    ) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="task")

        wrapped = wrap_final_response_node(
            _make_final_response_fn("SUCCESS", "clean output"), log, "s1"
        )
        result = wrapped(state)
        merged = state.model_copy(update={"outcome_state": result["outcome_state"]})

        p = project_state("s1", log)
        report = check_state_consistency(p, merged)

        assert p.task_completed_outcome == "SUCCESS"
        assert "task_completed_outcome" not in report.mismatch_fields()

    def test_denied_path_consistent(self) -> None:
        log = ExecutionEventLog()
        state = RuntimeState(user_request="blocked task")

        wrapped = wrap_final_response_node(
            _make_final_response_fn("DENIED", "capability_policy"), log, "s1"
        )
        result = wrapped(state)
        merged = state.model_copy(update={"outcome_state": result["outcome_state"]})

        p = project_state("s1", log)
        report = check_state_consistency(p, merged)

        assert p.task_completed_outcome == "DENIED"
        assert p.outcome == "failed"
        assert "task_completed_outcome" not in report.mismatch_fields()
