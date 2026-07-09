"""P29 — eval_score/passed + reflection_summary Event Migration.

Verifies that wrap_evaluation_node overrides evaluation_result.score/passed,
and wrap_reflection_node overrides semantic_state.reflection_summary, with
event-derived values so that ExecutionEventLog is the source of truth.

Tests cover:
  1. wrap_evaluation_node — event emission, state override, legacy mode
  2. wrap_reflection_node — event emission, state override, success path, legacy mode
  3. Consistency (check_state_consistency passes on migrated fields)
  4. Projection agreement (project_state matches state after migration)
"""

from __future__ import annotations

import pytest

from reforge.tests._consistency import check_state_consistency
from reforge.runtime.events.emitters import wrap_evaluation_node, wrap_reflection_node
from reforge.runtime.events.log import ExecutionEventLog
from reforge.runtime.events.projection import project_state
from reforge.runtime.domain.state.models import ExecutionState, RuntimeState, SemanticState


# ---------------------------------------------------------------------------
# Helpers — fake node functions
# ---------------------------------------------------------------------------


def _eval_node(score: float = 0.85, passed: bool = True):
    def node(state: RuntimeState) -> dict:
        return {
            "evaluation_result": {
                "passed": passed,
                "score": score,
                "checks": [],
                "summary": "ok",
                "failure_type": "",
            },
            "attempts": [],
        }
    return node


def _reflection_node(summary: str = "missing colon"):
    def node(state: RuntimeState) -> dict:
        return {
            "reflection_result": {
                "error_type": "SyntaxError",
                "error_summary": summary,
                "suggested_fix": "add colon",
            },
            "semantic_state": SemanticState(reflection_summary=summary),
        }
    return node


def _reflection_success_node(state: RuntimeState) -> dict:
    return {
        "reflection_result": {
            "error_type": "",
            "error_summary": "Execution succeeded",
            "suggested_fix": "",
        },
        "semantic_state": SemanticState(reflection_summary="Execution succeeded"),
    }


def _state_with_traceback(tb: str = "SyntaxError: invalid syntax") -> RuntimeState:
    return RuntimeState(
        user_request="run code",
        exec_state=ExecutionState(stderr=tb, exit_code=1),
    )


# ---------------------------------------------------------------------------
# 1. wrap_evaluation_node
# ---------------------------------------------------------------------------


class TestWrapEvaluationNode:
    def test_legacy_returns_identity(self) -> None:
        fn = _eval_node()
        wrapped = wrap_evaluation_node(fn, None, "s1")
        assert wrapped is fn

    def test_event_emitted(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(0.8, True), log, "s1")
        wrapped(RuntimeState())
        events = log.query(kind="EVALUATION_COMPLETED", session_id="s1")
        assert len(events) == 1

    def test_event_payload_score(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(0.75, False), log, "s1")
        wrapped(RuntimeState())
        ev = log.query(kind="EVALUATION_COMPLETED", session_id="s1")[0]
        assert ev.payload["score"] == pytest.approx(0.75)

    def test_event_payload_passed(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(1.0, True), log, "s1")
        wrapped(RuntimeState())
        ev = log.query(kind="EVALUATION_COMPLETED", session_id="s1")[0]
        assert ev.payload["passed"] is True

    def test_state_score_overridden_with_event_value(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(0.6, False), log, "s1")
        result = wrapped(RuntimeState())
        assert result["evaluation_result"]["score"] == pytest.approx(0.6)

    def test_state_passed_overridden_with_event_value(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(1.0, True), log, "s1")
        result = wrapped(RuntimeState())
        assert result["evaluation_result"]["passed"] is True

    def test_state_score_matches_event(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(0.42, False), log, "s1")
        result = wrapped(RuntimeState())
        ev = log.query(kind="EVALUATION_COMPLETED", session_id="s1")[0]
        assert result["evaluation_result"]["score"] == pytest.approx(ev.payload["score"])

    def test_state_passed_matches_event(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(0.42, False), log, "s1")
        result = wrapped(RuntimeState())
        ev = log.query(kind="EVALUATION_COMPLETED", session_id="s1")[0]
        assert result["evaluation_result"]["passed"] == ev.payload["passed"]

    def test_other_eval_fields_preserved(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(0.5, False), log, "s1")
        result = wrapped(RuntimeState())
        assert result["evaluation_result"]["summary"] == "ok"
        assert result["evaluation_result"]["checks"] == []

    def test_session_isolation(self) -> None:
        log = ExecutionEventLog()
        wrap_evaluation_node(_eval_node(0.3, False), log, "s1")(RuntimeState())
        wrap_evaluation_node(_eval_node(0.9, True), log, "s2")(RuntimeState())
        ev1 = log.query(kind="EVALUATION_COMPLETED", session_id="s1")[0]
        ev2 = log.query(kind="EVALUATION_COMPLETED", session_id="s2")[0]
        assert ev1.payload["score"] == pytest.approx(0.3)
        assert ev2.payload["score"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# 2. wrap_reflection_node
# ---------------------------------------------------------------------------


class TestWrapReflectionNode:
    def test_legacy_returns_identity(self) -> None:
        fn = _reflection_node()
        wrapped = wrap_reflection_node(fn, None, "s1")
        assert wrapped is fn

    def test_event_emitted_on_traceback(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_reflection_node("root cause A"), log, "s1")
        wrapped(_state_with_traceback())
        events = log.query(kind="REFLECTION_GENERATED", session_id="s1")
        assert len(events) == 1
        assert events[0].payload["summary"] == "root cause A"

    def test_event_emitted_on_success_path_with_summary(self) -> None:
        # Success path: wrapper now emits if error_summary is non-empty
        # (_reflection_success_node returns error_summary="Execution succeeded")
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_reflection_success_node, log, "s1")
        wrapped(RuntimeState())
        events = log.query(kind="REFLECTION_GENERATED", session_id="s1")
        assert len(events) == 1
        assert events[0].payload["summary"] == "Execution succeeded"

    def test_no_event_on_empty_summary(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_reflection_node(summary=""), log, "s1")
        wrapped(_state_with_traceback())
        events = log.query(kind="REFLECTION_GENERATED", session_id="s1")
        assert len(events) == 0

    def test_semantic_state_overridden(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_reflection_node("missing import"), log, "s1")
        result = wrapped(_state_with_traceback())
        assert result["semantic_state"].reflection_summary == "missing import"

    def test_semantic_state_matches_event(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_reflection_node("bad indent"), log, "s1")
        result = wrapped(_state_with_traceback())
        ev = log.query(kind="REFLECTION_GENERATED", session_id="s1")[0]
        assert result["semantic_state"].reflection_summary == ev.payload["summary"]

    def test_override_on_success_path_with_summary(self) -> None:
        # Wrapper now overrides semantic_state on success path too (summary non-empty)
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_reflection_success_node, log, "s1")
        result = wrapped(RuntimeState())
        assert result["semantic_state"].reflection_summary == "Execution succeeded"

    def test_session_isolation(self) -> None:
        log = ExecutionEventLog()
        wrap_reflection_node(_reflection_node("error A"), log, "s1")(_state_with_traceback())
        wrap_reflection_node(_reflection_node("error B"), log, "s2")(_state_with_traceback())
        ev1 = log.query(kind="REFLECTION_GENERATED", session_id="s1")[0]
        ev2 = log.query(kind="REFLECTION_GENERATED", session_id="s2")[0]
        assert ev1.payload["summary"] == "error A"
        assert ev2.payload["summary"] == "error B"


# ---------------------------------------------------------------------------
# 3. Consistency and projection checks
# ---------------------------------------------------------------------------


class TestMigrationConsistency:
    def test_consistency_eval_score_passes(self) -> None:
        from reforge.runtime.domain.state.models import EvaluationResult, ExecutionState
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(0.9, True), log, "s1")
        wrapped(RuntimeState())
        state = RuntimeState(
            evaluation_result=EvaluationResult(score=0.9, passed=True),
            exec_state=ExecutionState(exit_code=None),
        )
        proj = project_state("s1", log)
        report = check_state_consistency(proj, state)
        assert "last_eval_score" not in report.mismatch_fields()
        assert "last_eval_passed" not in report.mismatch_fields()

    def test_consistency_reflection_passes(self) -> None:
        from reforge.runtime.domain.state.models import SemanticState
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_reflection_node("import error"), log, "s1")
        wrapped(_state_with_traceback())
        state = RuntimeState(
            semantic_state=SemanticState(reflection_summary="import error")
        )
        proj = project_state("s1", log)
        report = check_state_consistency(proj, state)
        assert "last_reflection" not in report.mismatch_fields()

    def test_projection_eval_score_matches_state(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_evaluation_node(_eval_node(0.65, False), log, "s1")
        result = wrapped(RuntimeState())
        proj = project_state("s1", log)
        assert proj.last_eval_score == pytest.approx(result["evaluation_result"]["score"])
        assert proj.last_eval_passed == result["evaluation_result"]["passed"]

    def test_projection_reflection_matches_state(self) -> None:
        log = ExecutionEventLog()
        wrapped = wrap_reflection_node(_reflection_node("syntax issue"), log, "s1")
        result = wrapped(_state_with_traceback())
        proj = project_state("s1", log)
        assert proj.last_reflection == result["semantic_state"].reflection_summary
