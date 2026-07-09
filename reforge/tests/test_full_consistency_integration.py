"""P33 — Full Consistency Integration Test.

End-to-end validation of the entire P26-P32 event-sourced migration:
all 4 emitter wrappers run sequentially, and check_state_consistency()
reports zero mismatches across all 7 mapped fields.

Mapped fields (from check_state_consistency):
  1. retry_count           ← control_state.retry_count
  2. last_policy_decision  ← control_state.retry_decision_action
  3. last_eval_score       ← evaluation_result.score
  4. last_eval_passed      ← evaluation_result.passed
  5. last_reflection       ← semantic_state.reflection_summary
  6. last_execution_outcome← derived from exec_state.exit_code
  7. current_attempt       ← len(state.attempts)

Scenarios:
  A. Clean success (ACCEPT)
  B. Single failure (STOP)
  C. Retry + success
  D. Max retries → STOP
  E. Two independent sessions
"""

from __future__ import annotations

import pytest

from reforge.tests._consistency import check_state_consistency
from reforge.runtime.events.emitters import (
    wrap_evaluation_node,
    wrap_execution_node,
    wrap_reflection_node,
    wrap_retry_decision_node,
)
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.projection import project_state
from reforge.runtime.domain.state.models import (
    AttemptRecord,
    ExecutionOutput,
    ExecutionState,
    RuntimeState,
)


# ---------------------------------------------------------------------------
# Fake node factories — no LLM, no governor
# ---------------------------------------------------------------------------


def _exec_node_fn(succeed: bool):
    exit_code = 0 if succeed else 1
    tb = "" if succeed else "SyntaxError: invalid syntax"

    def node(state: RuntimeState) -> dict:
        attempt_num = len(state.attempts) + 1
        return {
            "exec_state": ExecutionState(exit_code=exit_code),
            "traceback": tb,
            "execution_output": ExecutionOutput(exit_code=exit_code, stderr=tb),
            "attempts": state.attempts + [
                AttemptRecord(attempt=attempt_num, exit_code=exit_code)
            ],
        }
    return node


def _eval_node_fn(score: float, passed: bool):
    def node(state: RuntimeState) -> dict:
        return {
            "evaluation_result": {
                "score": score, "passed": passed,
                "checks": [], "summary": "", "failure_type": "",
            },
            "attempts": state.attempts,
        }
    return node


def _refl_node_fn(summary: str):
    def node(state: RuntimeState) -> dict:
        return {
            "reflection_result": {
                "error_type": "", "error_summary": summary, "suggested_fix": "",
            },
            "semantic_state": state.semantic_state.model_copy(
                update={"reflection_summary": summary}
            ),
        }
    return node


def _policy_node_fn(action: str, reason: str = "test reason"):
    def node(state: RuntimeState) -> dict:
        return {
            "retry_decision": {"action": action, "reason": reason},
            "control_state": state.control_state.model_copy(
                update={"policy_reason": reason}
            ),
        }
    return node


# ---------------------------------------------------------------------------
# Pipeline helpers — build (RuntimeState, EventLog) for each scenario
# ---------------------------------------------------------------------------


def _merge(state: RuntimeState, result: dict) -> RuntimeState:
    """Simulate LangGraph top-level dict merge."""
    dump = state.model_dump()
    for key, val in result.items():
        if hasattr(val, "model_dump"):
            dump[key] = val.model_dump()
        elif isinstance(val, list):
            dump[key] = [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in val
            ]
        else:
            dump[key] = val
    return RuntimeState.model_validate(dump)


def _pipeline_success(sid: str) -> tuple[RuntimeState, ExecutionEventLog]:
    """One clean success attempt: STARTED→SUCCEEDED→EVAL(1.0,True)→POLICY(ACCEPT)."""
    log = ExecutionEventLog()
    state = RuntimeState(user_request="run code")

    state = _merge(state, wrap_execution_node(_exec_node_fn(True), log, sid)(state))
    state = _merge(state, wrap_evaluation_node(_eval_node_fn(1.0, True), log, sid)(state))
    state = _merge(state, wrap_reflection_node(_refl_node_fn("Execution succeeded"), log, sid)(state))
    state = _merge(state, wrap_retry_decision_node(_policy_node_fn("ACCEPT"), log, sid)(state))

    return state, log


def _pipeline_failure_stop(sid: str) -> tuple[RuntimeState, ExecutionEventLog]:
    """One failed attempt ending in STOP."""
    log = ExecutionEventLog()
    state = RuntimeState(user_request="run code")

    state = _merge(state, wrap_execution_node(_exec_node_fn(False), log, sid)(state))
    state = _merge(state, wrap_evaluation_node(_eval_node_fn(0.2, False), log, sid)(state))
    state = _merge(state, wrap_reflection_node(_refl_node_fn("missing import"), log, sid)(state))
    state = _merge(state, wrap_retry_decision_node(_policy_node_fn("STOP"), log, sid)(state))

    return state, log


def _pipeline_retry_then_success(sid: str) -> tuple[RuntimeState, ExecutionEventLog]:
    """Attempt 1: fail→RETRY. Attempt 2: succeed→ACCEPT."""
    log = ExecutionEventLog()
    state = RuntimeState(user_request="run code")

    # Attempt 1
    state = _merge(state, wrap_execution_node(_exec_node_fn(False), log, sid)(state))
    state = _merge(state, wrap_evaluation_node(_eval_node_fn(0.3, False), log, sid)(state))
    state = _merge(state, wrap_reflection_node(_refl_node_fn("syntax error"), log, sid)(state))
    state = _merge(state, wrap_retry_decision_node(_policy_node_fn("RETRY"), log, sid)(state))

    # Attempt 2 — success: reflection uses non-empty "Execution succeeded" summary
    state = _merge(state, wrap_execution_node(_exec_node_fn(True), log, sid)(state))
    state = _merge(state, wrap_evaluation_node(_eval_node_fn(1.0, True), log, sid)(state))
    state = _merge(state, wrap_reflection_node(_refl_node_fn("Execution succeeded"), log, sid)(state))
    state = _merge(state, wrap_retry_decision_node(_policy_node_fn("ACCEPT"), log, sid)(state))

    return state, log


def _pipeline_max_retries_stop(sid: str, n_retries: int = 2) -> tuple[RuntimeState, ExecutionEventLog]:
    """N failed retries then STOP."""
    log = ExecutionEventLog()
    state = RuntimeState(user_request="run code")

    for _ in range(n_retries):
        state = _merge(state, wrap_execution_node(_exec_node_fn(False), log, sid)(state))
        state = _merge(state, wrap_evaluation_node(_eval_node_fn(0.1, False), log, sid)(state))
        state = _merge(state, wrap_reflection_node(_refl_node_fn("runtime error"), log, sid)(state))
        state = _merge(state, wrap_retry_decision_node(_policy_node_fn("RETRY"), log, sid)(state))

    # Final attempt — STOP
    state = _merge(state, wrap_execution_node(_exec_node_fn(False), log, sid)(state))
    state = _merge(state, wrap_evaluation_node(_eval_node_fn(0.0, False), log, sid)(state))
    state = _merge(state, wrap_reflection_node(_refl_node_fn("still failing"), log, sid)(state))
    state = _merge(state, wrap_retry_decision_node(_policy_node_fn("STOP"), log, sid)(state))

    return state, log


# ---------------------------------------------------------------------------
# A. Clean success
# ---------------------------------------------------------------------------


class TestScenarioSuccess:
    def test_all_fields_consistent(self) -> None:
        state, log = _pipeline_success("s1")
        proj = project_state("s1", log)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, f"Mismatches: {report.mismatch_fields()}"

    def test_retry_count_zero(self) -> None:
        state, log = _pipeline_success("s1")
        proj = project_state("s1", log)
        assert proj.retry_count == 0
        assert state.control_state.retry_count == 0

    def test_policy_decision_accept(self) -> None:
        state, log = _pipeline_success("s1")
        proj = project_state("s1", log)
        assert proj.last_policy_decision == "ACCEPT"
        assert state.control_state.retry_decision_action == "ACCEPT"

    def test_eval_fields_match(self) -> None:
        state, log = _pipeline_success("s1")
        proj = project_state("s1", log)
        assert proj.last_eval_score == pytest.approx(1.0)
        assert state.semantic_state.evaluation_result.score == pytest.approx(1.0)
        assert proj.last_eval_passed is True
        assert state.semantic_state.evaluation_result.passed is True

    def test_execution_outcome_succeeded(self) -> None:
        state, log = _pipeline_success("s1")
        proj = project_state("s1", log)
        assert proj.last_execution_outcome == "succeeded"
        assert state.exec_state.exit_code == 0

    def test_current_attempt_is_one(self) -> None:
        state, log = _pipeline_success("s1")
        proj = project_state("s1", log)
        assert proj.current_attempt == 1
        assert len(state.attempts) == 1


# ---------------------------------------------------------------------------
# B. Single failure → STOP
# ---------------------------------------------------------------------------


class TestScenarioFailureStop:
    def test_all_fields_consistent(self) -> None:
        state, log = _pipeline_failure_stop("s1")
        proj = project_state("s1", log)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, f"Mismatches: {report.mismatch_fields()}"

    def test_policy_decision_stop(self) -> None:
        state, log = _pipeline_failure_stop("s1")
        proj = project_state("s1", log)
        assert proj.last_policy_decision == "STOP"
        assert state.control_state.retry_decision_action == "STOP"

    def test_eval_failed(self) -> None:
        state, log = _pipeline_failure_stop("s1")
        proj = project_state("s1", log)
        assert proj.last_eval_score == pytest.approx(0.2)
        assert proj.last_eval_passed is False

    def test_reflection_captured(self) -> None:
        state, log = _pipeline_failure_stop("s1")
        proj = project_state("s1", log)
        assert "missing import" in proj.last_reflection
        assert "missing import" in state.semantic_state.reflection_summary

    def test_execution_outcome_failed(self) -> None:
        state, log = _pipeline_failure_stop("s1")
        proj = project_state("s1", log)
        assert proj.last_execution_outcome == "failed"
        assert state.exec_state.exit_code != 0


# ---------------------------------------------------------------------------
# C. Retry then success
# ---------------------------------------------------------------------------


class TestScenarioRetrySuccess:
    def test_all_fields_consistent(self) -> None:
        state, log = _pipeline_retry_then_success("s1")
        proj = project_state("s1", log)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, f"Mismatches: {report.mismatch_fields()}"

    def test_retry_count_is_one(self) -> None:
        state, log = _pipeline_retry_then_success("s1")
        proj = project_state("s1", log)
        assert proj.retry_count == 1
        assert state.control_state.retry_count == 1

    def test_final_policy_is_accept(self) -> None:
        state, log = _pipeline_retry_then_success("s1")
        proj = project_state("s1", log)
        assert proj.last_policy_decision == "ACCEPT"

    def test_final_eval_is_latest(self) -> None:
        state, log = _pipeline_retry_then_success("s1")
        proj = project_state("s1", log)
        assert proj.last_eval_score == pytest.approx(1.0)
        assert proj.last_eval_passed is True

    def test_two_attempts_recorded(self) -> None:
        state, log = _pipeline_retry_then_success("s1")
        proj = project_state("s1", log)
        assert proj.current_attempt == 2
        assert len(state.attempts) == 2

    def test_outcome_is_succeeded(self) -> None:
        state, log = _pipeline_retry_then_success("s1")
        proj = project_state("s1", log)
        assert proj.outcome == "succeeded"


# ---------------------------------------------------------------------------
# D. Max retries → STOP
# ---------------------------------------------------------------------------


class TestScenarioMaxRetriesStop:
    def test_all_fields_consistent_two_retries(self) -> None:
        state, log = _pipeline_max_retries_stop("s1", n_retries=2)
        proj = project_state("s1", log)
        report = check_state_consistency(proj, state)
        assert report.is_consistent, f"Mismatches: {report.mismatch_fields()}"

    def test_retry_count_matches_events(self) -> None:
        state, log = _pipeline_max_retries_stop("s1", n_retries=2)
        proj = project_state("s1", log)
        assert proj.retry_count == 2
        assert state.control_state.retry_count == 2

    def test_three_attempts_total(self) -> None:
        state, log = _pipeline_max_retries_stop("s1", n_retries=2)
        proj = project_state("s1", log)
        assert proj.current_attempt == 3
        assert len(state.attempts) == 3

    def test_final_policy_is_stop(self) -> None:
        state, log = _pipeline_max_retries_stop("s1", n_retries=2)
        proj = project_state("s1", log)
        assert proj.last_policy_decision == "STOP"
        assert state.control_state.retry_decision_action == "STOP"

    def test_outcome_is_failed(self) -> None:
        state, log = _pipeline_max_retries_stop("s1", n_retries=2)
        proj = project_state("s1", log)
        assert proj.outcome == "failed"


# ---------------------------------------------------------------------------
# E. Session isolation — two independent sessions
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    def test_two_sessions_both_consistent(self) -> None:
        alice_state, alice_log = _pipeline_success("alice")
        bob_state, bob_log = _pipeline_failure_stop("bob")

        alice_proj = project_state("alice", alice_log)
        bob_proj = project_state("bob", bob_log)

        alice_report = check_state_consistency(alice_proj, alice_state)
        bob_report = check_state_consistency(bob_proj, bob_state)

        assert alice_report.is_consistent, f"Alice: {alice_report.mismatch_fields()}"
        assert bob_report.is_consistent, f"Bob: {bob_report.mismatch_fields()}"

    def test_shared_log_sessions_both_consistent(self) -> None:
        shared_log = ExecutionEventLog()

        # Build both sessions into the same log
        alice_state = RuntimeState(user_request="alice task")
        alice_state = _merge(alice_state, wrap_execution_node(_exec_node_fn(True), shared_log, "alice")(alice_state))
        alice_state = _merge(alice_state, wrap_evaluation_node(_eval_node_fn(1.0, True), shared_log, "alice")(alice_state))
        alice_state = _merge(alice_state, wrap_retry_decision_node(_policy_node_fn("ACCEPT"), shared_log, "alice")(alice_state))

        bob_state = RuntimeState(user_request="bob task")
        bob_state = _merge(bob_state, wrap_execution_node(_exec_node_fn(False), shared_log, "bob")(bob_state))
        bob_state = _merge(bob_state, wrap_evaluation_node(_eval_node_fn(0.1, False), shared_log, "bob")(bob_state))
        bob_state = _merge(bob_state, wrap_retry_decision_node(_policy_node_fn("STOP"), shared_log, "bob")(bob_state))

        alice_proj = project_state("alice", shared_log)
        bob_proj = project_state("bob", shared_log)

        alice_report = check_state_consistency(alice_proj, alice_state)
        bob_report = check_state_consistency(bob_proj, bob_state)

        assert alice_report.is_consistent, f"Alice: {alice_report.mismatch_fields()}"
        assert bob_report.is_consistent, f"Bob: {bob_report.mismatch_fields()}"
