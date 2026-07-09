"""P28 — Event-Derived retry_count Migration.

Verifies that wrap_retry_decision_node overrides control_state.retry_count with
the event-derived count (RECOVERY_ATTEMPTED event count) so that ExecutionEventLog
becomes the source of truth for retry_count when an event log is active.

Tests cover:
  1. Event emission still works correctly (POLICY_DECIDED + RECOVERY_ATTEMPTED)
  2. retry_count in result matches event count, not just state mutation
  3. Multi-retry sequences produce accurate event-derived counts
  4. ACCEPT/STOP decisions do not emit RECOVERY_ATTEMPTED
  5. Consistency (check_state_consistency passes on retry_count after migration)
  6. project_state().retry_count == state.control_state.retry_count (projection agreement)
  7. Legacy mode (event_log=None) — identity wrapper unchanged
"""

from __future__ import annotations


from reforge.tests._consistency import check_state_consistency
from reforge.runtime.events.emitters import wrap_retry_decision_node
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.projection import project_state
from reforge.runtime.domain.state.models import RuntimeControlState, RuntimeState


# ---------------------------------------------------------------------------
# Helpers — fake node functions
# ---------------------------------------------------------------------------


def _retry_node(state: RuntimeState) -> dict:
    """Simulate retry_decision_node returning RETRY (old state-increment still present)."""
    new_count = state.control_state.retry_count + 1
    return {
        "retry_decision": {"action": "RETRY", "reason": "eval failed"},
        "control_state": state.control_state.model_copy(
            update={"retry_count": new_count, "retry_decision_action": "RETRY"}
        ),
    }


def _accept_node(state: RuntimeState) -> dict:
    """Simulate retry_decision_node returning ACCEPT."""
    return {
        "retry_decision": {"action": "ACCEPT", "reason": "clean run"},
        "control_state": state.control_state.model_copy(
            update={"retry_decision_action": "ACCEPT"}
        ),
    }


def _stop_node(state: RuntimeState) -> dict:
    """Simulate retry_decision_node returning STOP."""
    return {
        "retry_decision": {"action": "STOP", "reason": "max retries"},
        "control_state": state.control_state.model_copy(
            update={"retry_decision_action": "STOP"}
        ),
    }


def _make_state(retry_count: int = 0) -> RuntimeState:
    return RuntimeState(
        user_request="run some code",
        control_state=RuntimeControlState(retry_count=retry_count),
    )


# ---------------------------------------------------------------------------
# 1. Legacy mode — event_log=None
# ---------------------------------------------------------------------------


class TestLegacyMode:
    def test_none_event_log_returns_identity(self) -> None:
        wrapped = wrap_retry_decision_node(_retry_node, None, "s1")
        assert wrapped is _retry_node

    def test_none_event_log_accept_returns_identity(self) -> None:
        wrapped = wrap_retry_decision_node(_accept_node, None, "s1")
        assert wrapped is _accept_node


# ---------------------------------------------------------------------------
# 2. Event emission correctness
# ---------------------------------------------------------------------------


class TestEventEmission:
    def test_policy_decided_emitted(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        wrapped(_make_state())
        events = log.query(kind="POLICY_DECIDED", session_id="s1")
        assert len(events) == 1
        assert events[0].payload["decision"] == "RETRY"

    def test_recovery_attempted_emitted_on_retry(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        wrapped(_make_state())
        events = log.query(kind="RECOVERY_ATTEMPTED", session_id="s1")
        assert len(events) == 1
        assert events[0].payload["attempt"] == 1

    def test_no_recovery_attempted_on_accept(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_accept_node, log, "s1")
        wrapped(_make_state())
        events = log.query(kind="RECOVERY_ATTEMPTED", session_id="s1")
        assert len(events) == 0

    def test_no_recovery_attempted_on_stop(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_stop_node, log, "s1")
        wrapped(_make_state())
        events = log.query(kind="RECOVERY_ATTEMPTED", session_id="s1")
        assert len(events) == 0

    def test_recovery_attempt_number_increments(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        wrapped(_make_state(retry_count=0))
        wrapped(_make_state(retry_count=1))
        events = log.query(kind="RECOVERY_ATTEMPTED", session_id="s1")
        assert events[0].payload["attempt"] == 1
        assert events[1].payload["attempt"] == 2


# ---------------------------------------------------------------------------
# 3. Event-derived retry_count override
# ---------------------------------------------------------------------------


class TestEventDerivedRetryCount:
    def test_first_retry_count_equals_one(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        result = wrapped(_make_state(retry_count=0))
        assert result["control_state"].retry_count == 1

    def test_retry_count_equals_recovery_event_count(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        result = wrapped(_make_state(retry_count=0))
        event_count = len(log.query(kind="RECOVERY_ATTEMPTED", session_id="s1"))
        assert result["control_state"].retry_count == event_count

    def test_second_retry_count_equals_two(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        result1 = wrapped(_make_state(retry_count=0))
        result2 = wrapped(_make_state(retry_count=result1["control_state"].retry_count))
        assert result2["control_state"].retry_count == 2

    def test_second_retry_matches_event_count(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        r1 = wrapped(_make_state(retry_count=0))
        r2 = wrapped(_make_state(retry_count=r1["control_state"].retry_count))
        event_count = len(log.query(kind="RECOVERY_ATTEMPTED", session_id="s1"))
        assert r2["control_state"].retry_count == event_count == 2

    def test_three_retries_count_accurate(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        result = _make_state()
        for _ in range(3):
            r = wrapped(_make_state(retry_count=result.control_state.retry_count
                                    if hasattr(result, "control_state")
                                    else result["control_state"].retry_count))
            result = r
        event_count = len(log.query(kind="RECOVERY_ATTEMPTED", session_id="s1"))
        assert r["control_state"].retry_count == 3
        assert event_count == 3

    def test_accept_does_not_change_retry_count(self) -> None:
        log = ExecutionEventLog()
        state = _make_state(retry_count=1)
        wrapped = wrap_retry_decision_node(_accept_node, log, "s1")
        result = wrapped(state)
        assert result["control_state"].retry_count == 1  # unchanged

    def test_event_count_is_source_of_truth(self) -> None:
        """Prove: result retry_count equals event count, not just state+1."""
        log = ExecutionEventLog()
        # Pre-populate two RECOVERY_ATTEMPTED events (as if from a prior run)
        from reforge.runtime.events.models import recovery_attempted
        log.append(recovery_attempted("s1", "t", strategy="llm_retry", attempt=1))
        log.append(recovery_attempted("s1", "t", strategy="llm_retry", attempt=2))

        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        # State says retry_count=0, but log already has 2 events → after emit: 3
        result = wrapped(_make_state(retry_count=0))
        # state-mutation would give 0+1=1, but event-derived gives 3
        assert result["control_state"].retry_count == 3


# ---------------------------------------------------------------------------
# 4. Consistency with bridge.consistency and projection
# ---------------------------------------------------------------------------


class TestMigrationConsistency:
    def _apply_result(self, state: RuntimeState, result: dict) -> RuntimeState:
        merged = state.model_dump() | {"control_state": result["control_state"]}
        return RuntimeState.model_validate(merged)

    def test_consistency_retry_count_passes_after_single_retry(self) -> None:
        log = ExecutionEventLog()
        state = _make_state()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        result = wrapped(state)
        new_state = self._apply_result(state, result)
        proj = project_state("s1", log)
        report = check_state_consistency(proj, new_state)
        assert "retry_count" not in report.mismatch_fields()

    def test_projection_matches_state_after_retry(self) -> None:
        log = ExecutionEventLog()
        state = _make_state()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        result = wrapped(state)
        new_state = self._apply_result(state, result)
        proj = project_state("s1", log)
        assert proj.retry_count == new_state.control_state.retry_count

    def test_projection_matches_after_two_retries(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_retry_node, log, "s1")
        state = _make_state()
        r1 = wrapped(state)
        state1 = RuntimeState.model_validate(
            state.model_dump() | {"control_state": r1["control_state"]}
        )
        r2 = wrapped(state1)
        state2 = RuntimeState.model_validate(
            state1.model_dump() | {"control_state": r2["control_state"]}
        )
        proj = project_state("s1", log)
        assert proj.retry_count == state2.control_state.retry_count == 2

    def test_session_isolation_in_retry_count(self) -> None:
        log = ExecutionEventLog()
        wrapped_s1 = wrap_retry_decision_node(_retry_node, log, "s1")
        wrapped_s2 = wrap_retry_decision_node(_retry_node, log, "s2")
        wrapped_s1(_make_state())
        wrapped_s2(_make_state())
        wrapped_s2(_make_state(retry_count=1))
        # s1: 1 retry, s2: 2 retries
        assert len(log.query(kind="RECOVERY_ATTEMPTED", session_id="s1")) == 1
        assert len(log.query(kind="RECOVERY_ATTEMPTED", session_id="s2")) == 2
