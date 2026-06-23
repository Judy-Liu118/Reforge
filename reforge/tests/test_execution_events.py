"""P22 — Execution Event Model: ExecutionEvent / factory functions / ExecutionEventLog.

Test categories:
  1. ExecutionEvent — construction, immutability, auto-generated fields
  2. Factory functions — payload shape, required/optional fields, FailureCategory
  3. ExecutionEventLog — append, query (kind/session/both/none), replay,
                         sessions, thread safety
"""

from __future__ import annotations

import threading

import pytest

from reforge.runtime.events.models import (
    ExecutionEvent,
    evaluation_completed,
    execution_failed,
    execution_started,
    execution_succeeded,
    policy_decided,
    recovery_attempted,
    reflection_generated,
)
from reforge.runtime.events.log import ExecutionEventLog


# ---------------------------------------------------------------------------
# 1. ExecutionEvent core
# ---------------------------------------------------------------------------


class TestExecutionEvent:
    def test_kind_stored(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        assert e.kind == "EXECUTION_STARTED"

    def test_session_id_stored(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="abc")
        assert e.session_id == "abc"

    def test_event_id_auto_generated(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        assert e.event_id
        assert isinstance(e.event_id, str)

    def test_event_id_unique(self) -> None:
        e1 = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        e2 = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        assert e1.event_id != e2.event_id

    def test_timestamp_auto_generated(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        assert e.timestamp
        assert "T" in e.timestamp  # ISO format

    def test_timestamp_is_utc_iso(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        assert "+00:00" in e.timestamp or e.timestamp.endswith("Z") or "UTC" not in e.timestamp

    def test_payload_defaults_empty(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        assert e.payload == {}

    def test_immutable_cannot_set_kind(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        with pytest.raises((AttributeError, TypeError)):
            e.kind = "EXECUTION_FAILED"  # type: ignore[misc]

    def test_immutable_cannot_set_session_id(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1")
        with pytest.raises((AttributeError, TypeError)):
            e.session_id = "other"  # type: ignore[misc]

    def test_custom_event_id(self) -> None:
        e = ExecutionEvent(kind="EXECUTION_STARTED", session_id="s1", event_id="custom")
        assert e.event_id == "custom"


# ---------------------------------------------------------------------------
# 2. Factory functions
# ---------------------------------------------------------------------------


class TestExecutionStarted:
    def test_kind(self) -> None:
        assert execution_started("s1", "write_file").kind == "EXECUTION_STARTED"

    def test_session_id(self) -> None:
        assert execution_started("mysession", "t").session_id == "mysession"

    def test_payload_has_task(self) -> None:
        e = execution_started("s1", "write_file")
        assert e.payload["task"] == "write_file"


class TestExecutionSucceeded:
    def test_kind(self) -> None:
        assert execution_succeeded("s1", "t").kind == "EXECUTION_SUCCEEDED"

    def test_payload_task(self) -> None:
        assert execution_succeeded("s1", "mytask").payload["task"] == "mytask"

    def test_output_summary_default_empty(self) -> None:
        assert execution_succeeded("s1", "t").payload["output_summary"] == ""

    def test_output_summary_stored(self) -> None:
        e = execution_succeeded("s1", "t", output_summary="42 lines")
        assert e.payload["output_summary"] == "42 lines"


class TestExecutionFailed:
    def test_kind(self) -> None:
        e = execution_failed("s1", "t", category="syntax", recoverable=True, error="err")
        assert e.kind == "EXECUTION_FAILED"

    def test_payload_category(self) -> None:
        e = execution_failed("s1", "t", category="dependency", recoverable=False, error="no module")
        assert e.payload["category"] == "dependency"

    def test_payload_recoverable(self) -> None:
        e = execution_failed("s1", "t", category="timeout", recoverable=False, error="timed out")
        assert e.payload["recoverable"] is False

    def test_payload_error(self) -> None:
        e = execution_failed("s1", "t", category="runtime_error", recoverable=True, error="oops")
        assert e.payload["error"] == "oops"

    def test_semantic_meaning_default_empty(self) -> None:
        e = execution_failed("s1", "t", category="unknown", recoverable=False, error="x")
        assert e.payload["semantic_meaning"] == ""

    def test_semantic_meaning_stored(self) -> None:
        e = execution_failed(
            "s1", "t", category="dependency", recoverable=True,
            error="ModuleNotFoundError", semantic_meaning="missing_package",
        )
        assert e.payload["semantic_meaning"] == "missing_package"

    def test_all_failure_categories_valid(self) -> None:
        categories = ["dependency", "syntax", "runtime_error", "timeout", "policy_blocked", "unknown"]
        for cat in categories:
            e = execution_failed("s", "t", category=cat, recoverable=True, error="x")  # type: ignore[arg-type]
            assert e.payload["category"] == cat


class TestRecoveryAttempted:
    def test_kind(self) -> None:
        assert recovery_attempted("s1", "t", "llm_retry", 1).kind == "RECOVERY_ATTEMPTED"

    def test_payload_shape(self) -> None:
        e = recovery_attempted("s1", "mytask", "prompt_rewrite", 2)
        assert e.payload == {"task": "mytask", "strategy": "prompt_rewrite", "attempt": 2}


class TestEvaluationCompleted:
    def test_kind(self) -> None:
        assert evaluation_completed("s1", score=0.9, passed=True).kind == "EVALUATION_COMPLETED"

    def test_payload_score(self) -> None:
        e = evaluation_completed("s1", score=0.75, passed=False)
        assert e.payload["score"] == 0.75

    def test_payload_passed(self) -> None:
        assert evaluation_completed("s1", score=1.0, passed=True).payload["passed"] is True

    def test_reasons_default_empty_list(self) -> None:
        assert evaluation_completed("s1", score=0.5, passed=False).payload["reasons"] == []

    def test_reasons_stored(self) -> None:
        reasons = ["output_too_short", "retry_drift"]
        e = evaluation_completed("s1", score=0.2, passed=False, reasons=reasons)
        assert e.payload["reasons"] == reasons


class TestReflectionGenerated:
    def test_kind(self) -> None:
        assert reflection_generated("s1", "summary").kind == "REFLECTION_GENERATED"

    def test_payload_summary(self) -> None:
        e = reflection_generated("s1", "root cause: missing import")
        assert e.payload["summary"] == "root cause: missing import"


class TestPolicyDecided:
    def test_kind(self) -> None:
        assert policy_decided("s1", "RETRY", "eval failed").kind == "POLICY_DECIDED"

    def test_payload_shape(self) -> None:
        e = policy_decided("s1", "STOP", "max retries reached")
        assert e.payload == {"decision": "STOP", "reason": "max retries reached"}


# ---------------------------------------------------------------------------
# 3. ExecutionEventLog
# ---------------------------------------------------------------------------


class TestExecutionEventLog:
    def test_initial_len_zero(self) -> None:
        assert len(ExecutionEventLog()) == 0

    def test_append_increases_len(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        assert len(log) == 1

    def test_replay_returns_all_in_order(self) -> None:
        log = ExecutionEventLog()
        e1 = execution_started("s1", "t1")
        e2 = execution_succeeded("s1", "t1")
        log.append(e1)
        log.append(e2)
        assert log.replay() == [e1, e2]

    def test_replay_is_snapshot(self) -> None:
        log = ExecutionEventLog()
        e1 = execution_started("s1", "t")
        log.append(e1)
        snap = log.replay()
        log.append(execution_succeeded("s1", "t"))
        assert len(snap) == 1  # snapshot not affected by later appends

    def test_query_by_kind(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_succeeded("s1", "t"))
        log.append(execution_started("s2", "t"))
        results = log.query(kind="EXECUTION_STARTED")
        assert len(results) == 2
        assert all(e.kind == "EXECUTION_STARTED" for e in results)

    def test_query_by_session_id(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_started("s2", "t"))
        results = log.query(session_id="s1")
        assert len(results) == 1
        assert results[0].session_id == "s1"

    def test_query_by_kind_and_session_id(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        log.append(execution_started("s2", "t"))
        log.append(execution_succeeded("s1", "t"))
        results = log.query(kind="EXECUTION_STARTED", session_id="s1")
        assert len(results) == 1
        assert results[0].kind == "EXECUTION_STARTED"
        assert results[0].session_id == "s1"

    def test_query_no_filter_returns_all(self) -> None:
        log = ExecutionEventLog()
        for _ in range(5):
            log.append(execution_started("s1", "t"))
        assert len(log.query()) == 5

    def test_query_unknown_kind_returns_empty(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        assert log.query(kind="POLICY_DECIDED") == []

    def test_query_unknown_session_returns_empty(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        assert log.query(session_id="ghost") == []

    def test_sessions_returns_all_session_ids(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("alice", "t"))
        log.append(execution_started("bob", "t"))
        assert log.sessions() == {"alice", "bob"}

    def test_sessions_empty_for_empty_log(self) -> None:
        assert ExecutionEventLog().sessions() == set()

    def test_multiple_events_same_session(self) -> None:
        log = ExecutionEventLog()
        for kind_fn in [
            lambda: execution_started("s1", "t"),
            lambda: execution_succeeded("s1", "t"),
            lambda: evaluation_completed("s1", score=1.0, passed=True),
        ]:
            log.append(kind_fn())
        assert len(log.query(session_id="s1")) == 3

    def test_thread_safety_concurrent_appends(self) -> None:
        log = ExecutionEventLog()
        errors: list[Exception] = []

        def worker():
            try:
                for i in range(50):
                    log.append(execution_started(f"s{i}", "t"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(log) == 500

    def test_query_returns_snapshot_not_live_view(self) -> None:
        log = ExecutionEventLog()
        log.append(execution_started("s1", "t"))
        snap = log.query(session_id="s1")
        log.append(execution_succeeded("s1", "t"))
        assert len(snap) == 1  # original snapshot unaffected
