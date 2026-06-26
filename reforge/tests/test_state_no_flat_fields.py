"""Contract tests for P-R.3 — RuntimeState flat backward-compat fields removed.

These tests guard against accidental re-introduction of dual-write flat fields.
The canonical paths are now nested sub-states (control/semantic/outcome/exec).

execution_output and traceback are read-only properties derived from exec_state —
they are NOT model fields (not in model_fields) but remain accessible as properties.
"""

from __future__ import annotations

from reforge.runtime.domain.state.models import ExecutionOutput, ExecutionState, RuntimeState


DELETED_FLAT_FIELDS = (
    "retry_count",
    "task_intent",
    "task_outcome",
    "outcome_reason",
    "final_answer",
    "execution_status",
    "task_status",
    "decision_reason",
    # Migrated to properties derived from exec_state:
    "execution_output",
    "traceback",
    # Migrated: action string lives in control_state.retry_decision_action
    "retry_decision",
    # P42: moved to semantic_state or removed entirely
    "evaluation_result",
    "reflection_result",
    "retry_context",
    "execution_policy",
    "governor_resolution",
)


class TestFlatFieldsRemoved:
    def test_runtime_state_has_no_flat_legacy_fields(self) -> None:
        """execution_output and traceback must NOT be model fields — they are properties."""
        declared = set(RuntimeState.model_fields.keys())
        for flat in DELETED_FLAT_FIELDS:
            assert flat not in declared, (
                f"RuntimeState.{flat} was removed but came back as a field. "
                f"It should be a read-only property derived from exec_state."
            )

    def test_required_nested_sub_states_present(self) -> None:
        declared = set(RuntimeState.model_fields.keys())
        for nested in ("exec_state", "control_state", "semantic_state", "outcome_state"):
            assert nested in declared, f"missing nested sub-state {nested}"

    def test_payload_fields_remain_top_level(self) -> None:
        """Pure input/output payloads stay flat — they have no sub-state home.

        image_inputs is a task-level input declared once by the caller through
        RuntimeRunner.run(image_inputs=...); it is intentionally NOT a governed
        fragment field. The "no flat field" rule is about removing legacy
        dual-write fields that duplicate nested sub-state, not about banning
        new top-level inputs.
        """
        declared = set(RuntimeState.model_fields.keys())
        for payload in (
            "user_request",
            "generated_code",
            "attempts",
            "task_requirements",
            "capability_decision",
            "classification_result",
            "image_inputs",
        ):
            assert payload in declared, (
                f"payload field {payload} should remain top-level"
            )

    def test_eval_and_reflection_live_in_semantic_state(self) -> None:
        """evaluation_result and reflection_result are owned by semantic_state, not RuntimeState."""
        from reforge.runtime.domain.state.models import SemanticState
        sem_fields = set(SemanticState.model_fields.keys())
        assert "evaluation_result" in sem_fields
        assert "reflection_result" in sem_fields

    def test_execution_output_is_property_not_field(self) -> None:
        """execution_output is a derived property of exec_state, not a model field."""
        state = RuntimeState(user_request="x")
        assert state.execution_output is None  # before execution
        state.exec_state = ExecutionState(stdout="hi", stderr="", exit_code=0)
        eo = state.execution_output
        assert isinstance(eo, ExecutionOutput)
        assert eo.stdout == "hi"
        assert eo.exit_code == 0

    def test_traceback_is_property_not_field(self) -> None:
        """traceback is derived from exec_state.stderr when exit_code != 0."""
        state = RuntimeState(user_request="x")
        assert state.traceback == ""  # before execution
        state.exec_state = ExecutionState(stderr="SyntaxError", exit_code=1)
        assert state.traceback == "SyntaxError"
        state.exec_state = ExecutionState(stderr="irrelevant", exit_code=0)
        assert state.traceback == ""  # success → no traceback


class TestNestedAccessIsCanonical:
    def test_default_state_uses_nested_defaults(self) -> None:
        state = RuntimeState(user_request="x")
        assert state.control_state.retry_count == 0
        assert state.semantic_state.task_intent is None
        assert state.outcome_state.task_outcome is None
        assert state.exec_state.exit_code is None

    def test_nested_write_persists(self) -> None:
        state = RuntimeState(user_request="x")
        state.outcome_state.task_outcome = "SUCCESS"
        state.control_state.retry_count = 2
        assert state.outcome_state.task_outcome == "SUCCESS"
        assert state.control_state.retry_count == 2
