"""Test Governor pipeline stages individually and integrated."""

from unittest.mock import Mock, patch

from reforge.runtime.orchestration.governor import (
    CapabilityStage,
    ClassifyStage,
    IntentStage,
    PolicyStage,
    RuntimeContext,
)
from reforge.runtime.orchestration.governor import ExecutionGovernor
from reforge.runtime.policy.task_intent import TaskIntent
from reforge.runtime.domain.state.models import EvaluationResult, ExecutionState, RuntimeState


def _ctx(user_request: str = "test", exit_code: int = 0) -> RuntimeContext:
    return RuntimeContext(
        state=RuntimeState(
            user_request=user_request,
            exec_state=ExecutionState(exit_code=exit_code, stdout="ok"),
            evaluation_result=EvaluationResult(passed=True),
        ),
        request=user_request,
    )


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
        assert ctx.capability_reason == "filesystem_destruction"

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
    def test_full_pipeline_normal(self):
        mock = Mock(return_value=TaskIntent.NORMAL_EXECUTION)
        with patch("reforge.runtime.orchestration.governor.intent_stage.classify_intent", mock):
            gov = ExecutionGovernor()
            r = gov.resolve(RuntimeState(
                user_request="test",
                exec_state=ExecutionState(exit_code=0, stdout="ok"),
                evaluation_result=EvaluationResult(passed=True),
            ))
            assert r.action == "ACCEPT"
            assert r.outcome == "SUCCESS"

    def test_full_pipeline_deny(self):
        mock = Mock(return_value=TaskIntent.NORMAL_EXECUTION)
        with patch("reforge.runtime.orchestration.governor.intent_stage.classify_intent", mock):
            gov = ExecutionGovernor()
            r = gov.resolve(RuntimeState(user_request="run rm -rf /"))
            assert r.action == "DENY"
            assert r.outcome == "DENIED"
