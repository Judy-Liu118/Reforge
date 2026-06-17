"""P23 — Event-Sourced Runtime Integration.

Tests cover:
  1. FailureCategorizer — pure mapping from (exit_code, stderr) → category
  2. wrap_execution_node — EXECUTION_STARTED + SUCCEEDED/FAILED emission
  3. wrap_evaluation_node — EVALUATION_COMPLETED emission
  4. wrap_reflection_node — REFLECTION_GENERATED on failure paths
  5. wrap_retry_decision_node — POLICY_DECIDED + RECOVERY_ATTEMPTED emission
  6. RuntimeRunner — event_log DI + session_id wiring
"""

from __future__ import annotations

import pytest

from reforge.runtime.events.categorizer import categorize_failure
from reforge.runtime.events.emitters import (
    wrap_evaluation_node,
    wrap_execution_node,
    wrap_reflection_node,
    wrap_retry_decision_node,
)
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.orchestration.engine.runner import RuntimeRunner
from reforge.runtime.domain.state.models import (
    ExecutionOutput,
    ExecutionState,
    RuntimeControlState,
    RuntimeState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**kwargs) -> RuntimeState:
    kwargs.setdefault("user_request", "test task")
    return RuntimeState(**kwargs)


def _mock_execution_success(state: RuntimeState) -> dict:
    return {
        "traceback": "",
        "execution_output": ExecutionOutput(stdout="ok", exit_code=0, duration_ms=1.0),
        "exec_state": ExecutionState(stdout="ok", exit_code=0),
        "attempts": [],
    }


def _mock_execution_failure(stderr: str, exit_code: int = 1):
    def node(state: RuntimeState) -> dict:
        return {
            "traceback": stderr,
            "execution_output": ExecutionOutput(stderr=stderr, exit_code=exit_code),
            "exec_state": ExecutionState(stderr=stderr, exit_code=exit_code),
            "attempts": [],
        }
    return node


def _mock_evaluation(score: float, passed: bool, failure_type: str = ""):
    def node(state: RuntimeState) -> dict:
        return {
            "evaluation_result": {
                "passed": passed,
                "score": score,
                "checks": [],
                "summary": "",
                "failure_type": failure_type,
            },
            "attempts": [],
        }
    return node


def _mock_reflection(summary: str):
    def node(state: RuntimeState) -> dict:
        return {
            "reflection_result": {
                "error_type": "RuntimeError",
                "error_summary": summary,
                "suggested_fix": "check the code",
            },
            "retry_context": {},
            "semantic_state": state.semantic_state,
        }
    return node


def _mock_retry_decision(action: str, reason: str = "test reason"):
    def node(state: RuntimeState) -> dict:
        return {
            "retry_decision": {"action": action, "reason": reason},
            "classification_result": {},
            "governor_resolution": {},
            "control_state": state.control_state,
            "semantic_state": state.semantic_state,
        }
    return node


# ---------------------------------------------------------------------------
# 1. FailureCategorizer
# ---------------------------------------------------------------------------


class TestFailureCategorizer:
    def test_exit_code_0_returns_unknown(self) -> None:
        cat, meaning = categorize_failure(0, "some stderr")
        assert cat == "unknown"
        assert meaning == ""

    def test_empty_stderr_returns_unknown(self) -> None:
        cat, _ = categorize_failure(1, "")
        assert cat == "unknown"

    def test_module_not_found_error(self) -> None:
        cat, meaning = categorize_failure(1, "ModuleNotFoundError: No module named 'numpy'")
        assert cat == "dependency"
        assert meaning == "missing_package"

    def test_import_error(self) -> None:
        cat, meaning = categorize_failure(1, "ImportError: cannot import name 'foo'")
        assert cat == "dependency"
        assert meaning == "import_error"

    def test_syntax_error(self) -> None:
        cat, meaning = categorize_failure(1, "SyntaxError: invalid syntax (line 3)")
        assert cat == "syntax"
        assert meaning == "syntax_error"

    def test_indentation_error(self) -> None:
        cat, meaning = categorize_failure(1, "IndentationError: unexpected indent")
        assert cat == "syntax"
        assert meaning == "syntax_error"

    def test_tab_error(self) -> None:
        cat, meaning = categorize_failure(1, "TabError: inconsistent use of tabs")
        assert cat == "syntax"
        assert meaning == "syntax_error"

    def test_timeout(self) -> None:
        cat, meaning = categorize_failure(1, "TimeoutError: execution timed out after 10s")
        assert cat == "timeout"
        assert meaning == "execution_timeout"

    def test_timed_out_phrase(self) -> None:
        cat, _ = categorize_failure(1, "Process timed out after 10 seconds")
        assert cat == "timeout"

    def test_permission_error(self) -> None:
        cat, meaning = categorize_failure(1, "PermissionError: [Errno 13] Permission denied")
        assert cat == "policy_blocked"
        assert meaning == "permission_denied"

    def test_name_error(self) -> None:
        cat, _ = categorize_failure(1, "NameError: name 'x' is not defined")
        assert cat == "runtime_error"

    def test_type_error(self) -> None:
        cat, _ = categorize_failure(1, "TypeError: unsupported operand type")
        assert cat == "runtime_error"

    def test_attribute_error(self) -> None:
        cat, _ = categorize_failure(1, "AttributeError: 'NoneType' has no attribute 'split'")
        assert cat == "runtime_error"

    def test_unknown_error_fallback(self) -> None:
        cat, _ = categorize_failure(1, "Something went terribly wrong")
        assert cat == "runtime_error"

    def test_case_insensitive_matching(self) -> None:
        cat, meaning = categorize_failure(1, "modulenotfounderror: no module named 'os'")
        assert cat == "dependency"
        assert meaning == "missing_package"


# ---------------------------------------------------------------------------
# 2. wrap_execution_node
# ---------------------------------------------------------------------------


class TestWrapExecutionNode:
    def test_none_log_returns_original_fn(self) -> None:
        fn = _mock_execution_success
        assert wrap_execution_node(fn, None, "s") is fn

    def test_success_emits_started_and_succeeded(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_execution_node(_mock_execution_success, log, "s1")
        wrapped(_state())
        kinds = [e.kind for e in log.replay()]
        assert kinds == ["EXECUTION_STARTED", "EXECUTION_SUCCEEDED"]

    def test_failure_emits_started_and_failed(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_execution_node(
            _mock_execution_failure("NameError: name 'x' not defined"), log, "s1"
        )
        wrapped(_state())
        kinds = [e.kind for e in log.replay()]
        assert kinds == ["EXECUTION_STARTED", "EXECUTION_FAILED"]

    def test_failure_event_has_category(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_execution_node(
            _mock_execution_failure("SyntaxError: invalid syntax"), log, "s1"
        )
        wrapped(_state())
        failed = log.query(kind="EXECUTION_FAILED")[0]
        assert failed.payload["category"] == "syntax"

    def test_failure_event_has_semantic_meaning(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_execution_node(
            _mock_execution_failure("ModuleNotFoundError: No module named 'requests'"), log, "s1"
        )
        wrapped(_state())
        failed = log.query(kind="EXECUTION_FAILED")[0]
        assert failed.payload["semantic_meaning"] == "missing_package"

    def test_failure_event_recoverable_true(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_execution_node(
            _mock_execution_failure("RuntimeError: boom"), log, "s1"
        )
        wrapped(_state())
        failed = log.query(kind="EXECUTION_FAILED")[0]
        assert failed.payload["recoverable"] is True

    def test_session_id_in_events(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_execution_node(_mock_execution_success, log, "my-session")
        wrapped(_state())
        assert all(e.session_id == "my-session" for e in log.replay())

    def test_task_from_user_request(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_execution_node(_mock_execution_success, log, "s1")
        wrapped(_state(user_request="print hello world"))
        started = log.query(kind="EXECUTION_STARTED")[0]
        assert "print hello world" in started.payload["task"]

    def test_original_result_preserved(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_execution_node(_mock_execution_success, log, "s1")
        result = wrapped(_state())
        assert result["traceback"] == ""
        assert result["execution_output"].exit_code == 0


# ---------------------------------------------------------------------------
# 3. wrap_evaluation_node
# ---------------------------------------------------------------------------


class TestWrapEvaluationNode:
    def test_none_log_returns_original_fn(self) -> None:
        fn = _mock_evaluation(1.0, True)
        assert wrap_evaluation_node(fn, None, "s") is fn

    def test_emits_evaluation_completed(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_mock_evaluation(0.8, True), log, "s1")
        wrapped(_state())
        assert len(log.query(kind="EVALUATION_COMPLETED")) == 1

    def test_payload_score_and_passed(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_mock_evaluation(0.6, False, "retry_drift"), log, "s1")
        wrapped(_state())
        ev = log.query(kind="EVALUATION_COMPLETED")[0]
        assert ev.payload["score"] == pytest.approx(0.6)
        assert ev.payload["passed"] is False

    def test_payload_reasons_from_failure_type(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_mock_evaluation(0.3, False, "output_too_short"), log, "s1")
        wrapped(_state())
        ev = log.query(kind="EVALUATION_COMPLETED")[0]
        assert "output_too_short" in ev.payload["reasons"]

    def test_payload_reasons_empty_when_passed(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_mock_evaluation(1.0, True), log, "s1")
        wrapped(_state())
        ev = log.query(kind="EVALUATION_COMPLETED")[0]
        assert ev.payload["reasons"] == []

    def test_session_id_in_event(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_mock_evaluation(1.0, True), log, "eval-session")
        wrapped(_state())
        assert log.query(kind="EVALUATION_COMPLETED")[0].session_id == "eval-session"


# ---------------------------------------------------------------------------
# 4. wrap_reflection_node
# ---------------------------------------------------------------------------


class TestWrapReflectionNode:
    def test_none_log_returns_original_fn(self) -> None:
        fn = _mock_reflection("some error")
        assert wrap_reflection_node(fn, None, "s") is fn

    def test_event_emitted_regardless_of_traceback_when_summary_nonempty(self) -> None:
        # P33: wrapper now emits on success path too when summary is non-empty
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_mock_reflection("summary"), log, "s1")
        wrapped(_state(traceback=""))  # no traceback = success path
        events = log.query(kind="REFLECTION_GENERATED")
        assert len(events) == 1
        assert events[0].payload["summary"] == "summary"

    def test_emits_reflection_generated_on_failure(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_mock_reflection("missing import"), log, "s1")
        wrapped(_state(traceback="ImportError: no module"))
        assert len(log.query(kind="REFLECTION_GENERATED")) == 1

    def test_payload_summary(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_mock_reflection("root cause: typo in line 5"), log, "s1")
        wrapped(_state(traceback="NameError: x"))
        ev = log.query(kind="REFLECTION_GENERATED")[0]
        assert ev.payload["summary"] == "root cause: typo in line 5"

    def test_no_event_when_summary_empty(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_mock_reflection(""), log, "s1")
        wrapped(_state(traceback="SyntaxError"))
        assert log.query(kind="REFLECTION_GENERATED") == []

    def test_session_id_in_event(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_mock_reflection("fix needed"), log, "refl-sid")
        wrapped(_state(traceback="err"))
        ev = log.query(kind="REFLECTION_GENERATED")[0]
        assert ev.session_id == "refl-sid"


# ---------------------------------------------------------------------------
# 5. wrap_retry_decision_node
# ---------------------------------------------------------------------------


class TestWrapRetryDecisionNode:
    def test_none_log_returns_original_fn(self) -> None:
        fn = _mock_retry_decision("ACCEPT")
        assert wrap_retry_decision_node(fn, None, "s") is fn

    def test_emits_policy_decided_on_accept(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_mock_retry_decision("ACCEPT", "clean run"), log, "s1")
        wrapped(_state())
        ev = log.query(kind="POLICY_DECIDED")[0]
        assert ev.payload["decision"] == "ACCEPT"
        assert ev.payload["reason"] == "clean run"

    def test_emits_policy_decided_on_stop(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_mock_retry_decision("STOP", "max retries"), log, "s1")
        wrapped(_state())
        assert log.query(kind="POLICY_DECIDED")[0].payload["decision"] == "STOP"

    def test_emits_policy_decided_on_retry(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_mock_retry_decision("RETRY", "eval failed"), log, "s1")
        wrapped(_state())
        assert log.query(kind="POLICY_DECIDED")[0].payload["decision"] == "RETRY"

    def test_retry_also_emits_recovery_attempted(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_mock_retry_decision("RETRY"), log, "s1")
        wrapped(_state())
        assert len(log.query(kind="RECOVERY_ATTEMPTED")) == 1

    def test_no_recovery_attempted_on_accept(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_mock_retry_decision("ACCEPT"), log, "s1")
        wrapped(_state())
        assert log.query(kind="RECOVERY_ATTEMPTED") == []

    def test_recovery_attempted_strategy_is_llm_retry(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_mock_retry_decision("RETRY"), log, "s1")
        wrapped(_state())
        ev = log.query(kind="RECOVERY_ATTEMPTED")[0]
        assert ev.payload["strategy"] == "llm_retry"

    def test_recovery_attempted_attempt_number(self) -> None:
        log = ExecutionEventLog()
        state = _state(
            control_state=RuntimeControlState(retry_count=2)
        )
        wrapped = wrap_retry_decision_node(_mock_retry_decision("RETRY"), log, "s1")
        wrapped(state)
        ev = log.query(kind="RECOVERY_ATTEMPTED")[0]
        assert ev.payload["attempt"] == 3  # retry_count + 1

    def test_session_id_in_events(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_mock_retry_decision("ACCEPT"), log, "pol-sid")
        wrapped(_state())
        assert log.query(kind="POLICY_DECIDED")[0].session_id == "pol-sid"


# ---------------------------------------------------------------------------
# 6. RuntimeRunner event_log wiring
# ---------------------------------------------------------------------------


class TestRuntimeRunnerEventLog:
    def test_runner_accepts_event_log(self) -> None:
        log = ExecutionEventLog()
        runner = RuntimeRunner(event_log=log)
        assert runner.event_log is log

    def test_runner_default_creates_internal_event_log(self) -> None:
        # P31: RuntimeRunner always creates an internal log when none is injected
        runner = RuntimeRunner()
        assert runner.event_log is not None
        from reforge.runtime.events.log import ExecutionEventLog as _EL
        assert isinstance(runner.event_log, _EL)

    def test_runner_session_id_is_consistent_string(self) -> None:
        runner = RuntimeRunner()
        assert isinstance(runner.session_id, str)
        assert len(runner.session_id) > 0

    def test_runner_session_id_unique_per_instance(self) -> None:
        r1 = RuntimeRunner()
        r2 = RuntimeRunner()
        assert r1.session_id != r2.session_id
