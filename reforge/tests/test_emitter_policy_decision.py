"""P30 — retry_decision_action Event Migration.

Verifies that wrap_retry_decision_node overrides control_state.retry_decision_action
with the action from the POLICY_DECIDED event, making ExecutionEventLog the source
of truth for the policy decision field.

Also verifies the P28 retry_count override is preserved after the P30 refactor
(both fields now updated in a single model_copy call).

Tests cover:
  1. retry_decision_action overridden for ACCEPT / STOP / RETRY
  2. retry_count still overridden for RETRY (P28 regression guard)
  3. Single model_copy sets both fields simultaneously on RETRY
  4. POLICY_DECIDED event payload matches state
  5. Consistency (check_state_consistency passes on last_policy_decision)
  6. Projection agreement (project_state.last_policy_decision matches state)
  7. Legacy mode (event_log=None) unchanged
"""

from __future__ import annotations


from reforge.tests._consistency import check_state_consistency
from reforge.runtime.events.emitters import wrap_retry_decision_node
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.projection import project_state
from reforge.runtime.domain.state.models import RuntimeControlState, RuntimeState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision_node(action: str, reason: str = "test reason"):
    def node(state: RuntimeState) -> dict:
        new_count = state.control_state.retry_count + (1 if action == "RETRY" else 0)
        return {
            "retry_decision": {"action": action, "reason": reason},
            "control_state": state.control_state.model_copy(
                update={"retry_count": new_count, "retry_decision_action": action}
            ),
        }
    return node


def _state(retry_count: int = 0) -> RuntimeState:
    return RuntimeState(
        user_request="run code",
        control_state=RuntimeControlState(retry_count=retry_count),
    )


def _apply(state: RuntimeState, result: dict) -> RuntimeState:
    merged = state.model_dump() | {"control_state": result["control_state"]}
    return RuntimeState.model_validate(merged)


# ---------------------------------------------------------------------------
# 1. retry_decision_action override
# ---------------------------------------------------------------------------


class TestPolicyDecisionOverride:
    def test_accept_sets_retry_decision_action(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("ACCEPT"), log, "s1")
        result = wrapped(_state())
        assert result["control_state"].retry_decision_action == "ACCEPT"

    def test_stop_sets_retry_decision_action(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("STOP"), log, "s1")
        result = wrapped(_state())
        assert result["control_state"].retry_decision_action == "STOP"

    def test_retry_sets_retry_decision_action(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("RETRY"), log, "s1")
        result = wrapped(_state())
        assert result["control_state"].retry_decision_action == "RETRY"

    def test_retry_decision_action_matches_event_payload(self) -> None:
        for action in ("ACCEPT", "STOP", "RETRY"):
            inner_log = ExecutionEventLog()
            wrapped = wrap_retry_decision_node(_decision_node(action), inner_log, "s1")
            result = wrapped(_state())
            ev = inner_log.query(kind="POLICY_DECIDED", session_id="s1")[0]
            assert result["control_state"].retry_decision_action == ev.payload["decision"]

    def test_policy_decided_event_emitted(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("ACCEPT"), log, "s1")
        wrapped(_state())
        events = log.query(kind="POLICY_DECIDED", session_id="s1")
        assert len(events) == 1
        assert events[0].payload["decision"] == "ACCEPT"

    def test_policy_event_reason_preserved(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("STOP", "max retries hit"), log, "s1")
        wrapped(_state())
        ev = log.query(kind="POLICY_DECIDED", session_id="s1")[0]
        assert ev.payload["reason"] == "max retries hit"


# ---------------------------------------------------------------------------
# 2. P28 regression — retry_count still overridden on RETRY
# ---------------------------------------------------------------------------


class TestRetryCountPreserved:
    def test_retry_count_still_derived_from_events(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("RETRY"), log, "s1")
        result = wrapped(_state(retry_count=0))
        assert result["control_state"].retry_count == 1

    def test_retry_count_and_action_set_simultaneously(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("RETRY"), log, "s1")
        result = wrapped(_state(retry_count=0))
        cs = result["control_state"]
        assert cs.retry_count == 1
        assert cs.retry_decision_action == "RETRY"

    def test_second_retry_both_fields_accurate(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("RETRY"), log, "s1")
        r1 = wrapped(_state(retry_count=0))
        r2 = wrapped(_state(retry_count=r1["control_state"].retry_count))
        assert r2["control_state"].retry_count == 2
        assert r2["control_state"].retry_decision_action == "RETRY"

    def test_accept_does_not_change_retry_count(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(_decision_node("ACCEPT"), log, "s1")
        result = wrapped(_state(retry_count=2))
        assert result["control_state"].retry_count == 2


# ---------------------------------------------------------------------------
# 3. Consistency and projection
# ---------------------------------------------------------------------------


class TestMigrationConsistency:
    def test_consistency_policy_decision_accept(self) -> None:
        log = ExecutionEventLog()
        state = _state()
        wrapped = wrap_retry_decision_node(_decision_node("ACCEPT"), log, "s1")
        result = wrapped(state)
        new_state = _apply(state, result)
        proj = project_state("s1", log)
        report = check_state_consistency(proj, new_state)
        assert "last_policy_decision" not in report.mismatch_fields()

    def test_consistency_policy_decision_stop(self) -> None:
        log = ExecutionEventLog()
        state = _state()
        wrapped = wrap_retry_decision_node(_decision_node("STOP"), log, "s1")
        result = wrapped(state)
        new_state = _apply(state, result)
        proj = project_state("s1", log)
        report = check_state_consistency(proj, new_state)
        assert "last_policy_decision" not in report.mismatch_fields()

    def test_consistency_retry_count_and_policy_both_pass(self) -> None:
        log = ExecutionEventLog()
        state = _state()
        wrapped = wrap_retry_decision_node(_decision_node("RETRY"), log, "s1")
        result = wrapped(state)
        new_state = _apply(state, result)
        proj = project_state("s1", log)
        report = check_state_consistency(proj, new_state)
        assert "retry_count" not in report.mismatch_fields()
        assert "last_policy_decision" not in report.mismatch_fields()

    def test_projection_last_policy_decision_matches_state(self) -> None:
        for action in ("ACCEPT", "STOP", "RETRY"):
            log = ExecutionEventLog()
            state = _state()
            wrapped = wrap_retry_decision_node(_decision_node(action), log, "s1")
            result = wrapped(state)
            new_state = _apply(state, result)
            proj = project_state("s1", log)
            assert proj.last_policy_decision == new_state.control_state.retry_decision_action

    def test_session_isolation(self) -> None:
        log = ExecutionEventLog()
        wrap_retry_decision_node(_decision_node("ACCEPT"), log, "s1")(_state())
        wrap_retry_decision_node(_decision_node("STOP"), log, "s2")(_state())
        ev1 = log.query(kind="POLICY_DECIDED", session_id="s1")[0]
        ev2 = log.query(kind="POLICY_DECIDED", session_id="s2")[0]
        assert ev1.payload["decision"] == "ACCEPT"
        assert ev2.payload["decision"] == "STOP"


# ---------------------------------------------------------------------------
# 4. Legacy mode
# ---------------------------------------------------------------------------


class TestLegacyMode:
    def test_none_event_log_returns_identity(self) -> None:
        fn = _decision_node("ACCEPT")
        assert wrap_retry_decision_node(fn, None, "s1") is fn
