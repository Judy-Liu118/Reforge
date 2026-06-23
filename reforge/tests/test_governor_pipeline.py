"""Test Governor pipeline — individual stages and end-to-end resolution."""

from unittest.mock import Mock, patch

from reforge.runtime.orchestration.governor import (
    CapabilityStage,
    ClassifyStage,
    ExecutionGovernor,
    IntentStage,
    PolicyStage,
    RuntimeContext,
)
from reforge.runtime.policy.task_intent import TaskIntent
from reforge.runtime.domain.state.models import (
    EvaluationResult,
    ExecutionState,
    RuntimeControlState,
    RuntimeState,
)


_INTENT_NORMAL = Mock(return_value=TaskIntent.NORMAL_EXECUTION)
_INTENT_EXPECTED = Mock(return_value=TaskIntent.EXPECTED_ERROR)
_INTENT_STRESS = Mock(return_value=TaskIntent.STRESS_TEST)
_INTENT_RECOVERABLE = Mock(return_value=TaskIntent.RECOVERABLE_DEMO)


def _ctx(user_request: str = "test", exit_code: int = 0) -> RuntimeContext:
    return RuntimeContext(
        state=RuntimeState(
            user_request=user_request,
            exec_state=ExecutionState(exit_code=exit_code, stdout="ok"),
            evaluation_result=EvaluationResult(passed=True),
        ),
        request=user_request,
    )


def _state(
    exit_code: int = 0,
    retry_count: int = 0,
    user_request: str = "test",
) -> RuntimeState:
    return RuntimeState(
        user_request=user_request,
        exec_state=ExecutionState(exit_code=exit_code, stdout="ok"),
        control_state=RuntimeControlState(retry_count=retry_count),
        evaluation_result=EvaluationResult(passed=True),
        classification_result={"intentional": False, "retryable": False, "failure_mode": "none"},
    )


def _resolve(state: RuntimeState, intent_mock: Mock = _INTENT_NORMAL):
    with patch("reforge.runtime.orchestration.governor.intent_stage.classify_intent", intent_mock):
        return ExecutionGovernor().resolve(state)


class TestStages:
    def test_intent_stage(self):
        mock = Mock(return_value=TaskIntent.NORMAL_EXECUTION)
        with patch("reforge.runtime.orchestration.governor.intent_stage.classify_intent", mock):
            ctx = IntentStage().execute(_ctx())
            assert ctx.task_intent == "NORMAL_EXECUTION"

    def test_capability_stage_allow(self):
        ctx = CapabilityStage().execute(_ctx("read sales.csv"))
        assert ctx.capability_allow

    def test_capability_stage_deny(self):
        ctx = CapabilityStage().execute(_ctx("run rm -rf /"))
        assert not ctx.capability_allow
        assert ctx.capability_deny_category == "filesystem_destruction"

    def test_classify_stage(self):
        ctx = _ctx()
        ctx.task_intent = "NORMAL_EXECUTION"
        ctx = ClassifyStage().execute(ctx)
        assert ctx.failure_mode == "none"
        assert not ctx.intentional
        assert not ctx.retryable

    def test_policy_stage(self):
        ctx = _ctx()
        ctx.task_intent = "NORMAL_EXECUTION"
        ctx.failure_mode = "none"
        ctx = PolicyStage().execute(ctx)
        assert ctx.policy_action == "ACCEPT"


class TestGovernorPipeline:
    def test_normal_success(self):
        r = _resolve(_state(exit_code=0))
        assert r.action == "ACCEPT"
        assert r.outcome == "SUCCESS"

    def test_normal_error_retry(self):
        r = _resolve(_state(exit_code=1, retry_count=0))
        assert r.action == "RETRY"

    def test_expected_error_stops(self):
        r = _resolve(_state(exit_code=1), intent_mock=_INTENT_EXPECTED)
        assert r.action == "STOP"
        assert r.outcome == "EXPECTED_FAILURE"

    def test_stress_test_success(self):
        r = _resolve(_state(exit_code=-1), intent_mock=_INTENT_STRESS)
        assert r.outcome == "SUCCESS"

    def test_capability_deny(self):
        r = _resolve(_state(user_request="run rm -rf / to delete everything"))
        assert r.action == "DENY"
        assert r.outcome == "DENIED"

    def test_recovered(self):
        r = _resolve(_state(exit_code=0, retry_count=1), intent_mock=_INTENT_RECOVERABLE)
        assert r.outcome == "RECOVERED"

    def test_repair_hint_survives_policy_stage(self):
        # PolicyStage overwrites ctx.outcome_reason unconditionally; the
        # repair hint from ClassifyStage must travel on its own field so it
        # actually reaches the final RuntimeResolution.
        from reforge.memory.execution_memory import ExecutionRecord

        fake_record = ExecutionRecord(
            request="analyze sales",
            outcome="RECOVERED",
            failure_mode="execution_error",
            retryable=True,
            repair_strategy="check the CSV path is relative to cwd",
        )
        mock_intent = Mock(return_value=TaskIntent.NORMAL_EXECUTION)
        with patch(
            "reforge.runtime.orchestration.governor.intent_stage.classify_intent",
            mock_intent,
        ), patch(
            "reforge.memory.execution_memory.ExecutionMemory.recall_similar",
            return_value=[fake_record],
        ):
            gov = ExecutionGovernor()
            r = gov.resolve(RuntimeState(
                user_request="analyze sales",
                exec_state=ExecutionState(exit_code=1, stderr="boom"),
                evaluation_result=EvaluationResult(passed=False, score=0.3),
            ))

        assert r.repair_hint == "check the CSV path is relative to cwd"
        assert r.reason != r.repair_hint  # outcome_reason still owned by PolicyStage
