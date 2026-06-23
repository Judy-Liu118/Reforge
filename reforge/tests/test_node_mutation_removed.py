"""P32 — Node Mutation Removal: retry_decision_node Phase 2.

After P31 (always-active EventLog) emitter overrides are guaranteed to run.
P32 removes the now-dead mutations from retry_decision_node:
  - retry_count increment (new_count = ... + control_state_retry)
  - retry_decision_action assignment
  - the entire control_state_retry model_copy branch

Tests verify:
  1. Bare node: retry_decision_action NOT set (None after removal)
  2. Bare node: retry_count unchanged (emitter's job now)
  3. Bare node: policy_reason still set (not migrated)
  4. Bare node: retry_decision dict still correct (routing still works)
  5. Wrapped node: emitter correctly fills retry_decision_action and retry_count
  6. Wrapped node using "stripped" fake (proves emitter-only path)
  7. should_retry routing unchanged
  8. P28/P30 regression: wrapped node consistency still passes
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from reforge.runtime.bridge.consistency import check_state_consistency
from reforge.runtime.events.emitters import wrap_retry_decision_node
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.projection import project_state
from reforge.runtime.orchestration.graph.nodes.retry_decision import retry_decision_node, should_retry
from reforge.runtime.domain.state.models import RuntimeControlState, RuntimeState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_resolution(action: str, reason: str = "test") -> MagicMock:
    """Build a fake RuntimeResolution."""
    r = MagicMock()
    r.action = action
    r.reason = reason
    r.task_intent = "general"
    r.failure_mode = ""
    r.intentional = False
    r.retryable = action == "RETRY"
    r.model_dump.return_value = {
        "action": action, "reason": reason, "task_intent": "general",
        "failure_mode": "", "intentional": False, "retryable": r.retryable,
    }
    return r


def _call_bare_node(action: str, retry_count: int = 0) -> tuple[dict, RuntimeState]:
    """Call retry_decision_node with a mocked governor returning *action*."""
    state = RuntimeState(
        user_request="test task",
        control_state=RuntimeControlState(retry_count=retry_count),
    )
    with patch("reforge.runtime.orchestration.graph.nodes.retry_decision.ExecutionGovernor") as MockGov:
        MockGov.return_value.resolve.return_value = _mock_resolution(action)
        result = retry_decision_node(state)
    return result, state


# ---------------------------------------------------------------------------
# 1. Bare node — migrated fields NOT set
# ---------------------------------------------------------------------------


class TestBareNodeNoMutation:
    def test_accept_does_not_set_retry_decision_action(self) -> None:
        result, _ = _call_bare_node("ACCEPT")
        # emitter hasn't run — action should remain as whatever input state had
        assert result["control_state"].retry_decision_action is None

    def test_stop_does_not_set_retry_decision_action(self) -> None:
        result, _ = _call_bare_node("STOP")
        assert result["control_state"].retry_decision_action is None

    def test_retry_does_not_set_retry_decision_action(self) -> None:
        result, _ = _call_bare_node("RETRY")
        assert result["control_state"].retry_decision_action is None

    def test_retry_does_not_increment_retry_count(self) -> None:
        result, state = _call_bare_node("RETRY", retry_count=0)
        assert result["control_state"].retry_count == 0  # unchanged

    def test_multiple_retries_count_not_incremented_by_node(self) -> None:
        result, _ = _call_bare_node("RETRY", retry_count=2)
        assert result["control_state"].retry_count == 2  # still 2, emitter would make it 3


# ---------------------------------------------------------------------------
# 2. Bare node — non-migrated fields still set
# ---------------------------------------------------------------------------


class TestBareNodeNonMigratedFields:
    def test_policy_reason_still_set(self) -> None:
        state = RuntimeState(user_request="task")
        with patch("reforge.runtime.orchestration.graph.nodes.retry_decision.ExecutionGovernor") as MockGov:
            MockGov.return_value.resolve.return_value = _mock_resolution("ACCEPT", "clean run")
            result = retry_decision_node(state)
        assert result["control_state"].policy_reason == "clean run"

    def test_retry_decision_dict_present(self) -> None:
        result, _ = _call_bare_node("ACCEPT")
        assert "retry_decision" in result
        assert result["retry_decision"]["action"] == "ACCEPT"

    def test_classification_result_present(self) -> None:
        result, _ = _call_bare_node("ACCEPT")
        assert "classification_result" in result

    def test_retry_context_not_in_result(self) -> None:
        """retry_context was removed in P42 — no longer a node output."""
        result, _ = _call_bare_node("RETRY")
        assert "retry_context" not in result

    def test_governor_resolution_not_in_result(self) -> None:
        """governor_resolution was removed in P42 — vestigial field."""
        result, _ = _call_bare_node("ACCEPT")
        assert "governor_resolution" not in result


# ---------------------------------------------------------------------------
# 3. Wrapped node — emitter fills migrated fields correctly
# ---------------------------------------------------------------------------


class TestWrappedNodeAfterRemoval:
    def _stripped_node(self, action: str, reason: str = "reason"):
        """Simulates post-P32 bare node: only sets policy_reason + retry_decision."""
        def node(state: RuntimeState) -> dict:
            return {
                "retry_decision": {"action": action, "reason": reason},
                "control_state": state.control_state.model_copy(
                    update={"policy_reason": reason}
                ),
            }
        return node

    def test_emitter_sets_retry_decision_action_accept(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(self._stripped_node("ACCEPT"), log, "s1")
        result = wrapped(RuntimeState(user_request="t"))
        assert result["control_state"].retry_decision_action == "ACCEPT"

    def test_emitter_sets_retry_decision_action_stop(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(self._stripped_node("STOP"), log, "s1")
        result = wrapped(RuntimeState(user_request="t"))
        assert result["control_state"].retry_decision_action == "STOP"

    def test_emitter_sets_retry_count_on_retry(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(self._stripped_node("RETRY"), log, "s1")
        result = wrapped(RuntimeState(user_request="t"))
        assert result["control_state"].retry_count == 1

    def test_emitter_sets_retry_count_correctly_on_second_retry(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(self._stripped_node("RETRY"), log, "s1")
        state = RuntimeState(user_request="t", control_state=RuntimeControlState(retry_count=0))
        r1 = wrapped(state)
        state2 = RuntimeState.model_validate(
            state.model_dump() | {"control_state": r1["control_state"]}
        )
        r2 = wrapped(state2)
        assert r2["control_state"].retry_count == 2

    def test_policy_reason_preserved_through_emitter(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_retry_decision_node(self._stripped_node("ACCEPT", "clean exec"), log, "s1")
        result = wrapped(RuntimeState(user_request="t"))
        assert result["control_state"].policy_reason == "clean exec"


# ---------------------------------------------------------------------------
# 4. should_retry routing unchanged
# ---------------------------------------------------------------------------


class TestShouldRetryUnchanged:
    def test_retry_routes_to_code_generation(self) -> None:
        state = RuntimeState(control_state=RuntimeControlState(retry_decision_action="RETRY"))
        assert should_retry(state) == "code_generation"

    def test_accept_routes_to_final_response(self) -> None:
        state = RuntimeState(control_state=RuntimeControlState(retry_decision_action="ACCEPT"))
        assert should_retry(state) == "final_response"

    def test_stop_routes_to_final_response(self) -> None:
        state = RuntimeState(control_state=RuntimeControlState(retry_decision_action="STOP"))
        assert should_retry(state) == "final_response"

    def test_no_decision_routes_to_final_response(self) -> None:
        assert should_retry(RuntimeState()) == "final_response"


# ---------------------------------------------------------------------------
# 5. Consistency regression
# ---------------------------------------------------------------------------


class TestConsistencyRegression:
    def _apply(self, state: RuntimeState, result: dict) -> RuntimeState:
        merged = state.model_dump() | {"control_state": result["control_state"]}
        return RuntimeState.model_validate(merged)

    def test_consistency_passes_after_accept(self) -> None:
        log = ExecutionEventLog()
        node = lambda s: {
            "retry_decision": {"action": "ACCEPT", "reason": "ok"},
            "control_state": s.control_state.model_copy(update={"policy_reason": "ok"}),
        }
        wrapped = wrap_retry_decision_node(node, log, "s1")
        state = RuntimeState(user_request="t")
        result = wrapped(state)
        new_state = self._apply(state, result)
        proj = project_state("s1", log)
        report = check_state_consistency(proj, new_state)
        assert "retry_count" not in report.mismatch_fields()
        assert "last_policy_decision" not in report.mismatch_fields()

    def test_consistency_passes_after_retry(self) -> None:
        log = ExecutionEventLog()
        node = lambda s: {
            "retry_decision": {"action": "RETRY", "reason": "fail"},
            "control_state": s.control_state.model_copy(update={"policy_reason": "fail"}),
        }
        wrapped = wrap_retry_decision_node(node, log, "s1")
        state = RuntimeState(user_request="t")
        result = wrapped(state)
        new_state = self._apply(state, result)
        proj = project_state("s1", log)
        report = check_state_consistency(proj, new_state)
        assert "retry_count" not in report.mismatch_fields()
        assert "last_policy_decision" not in report.mismatch_fields()
