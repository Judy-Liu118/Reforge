"""L3 history-based unrecoverability — repeated-signature deliberate STOP.

Covers the full chain: reflection appends per-attempt fingerprints to
semantic_state.failure_signature_history → ClassifyStage flips the
classification to failure_mode="repeated_signature" when consecutive
attempts share one structural fingerprint → RetryPolicy issues a
deliberate STOP with budget remaining → outcome_resolver reports
"repeated_failure_signature" instead of mislabeling it RETRIES_EXHAUSTED.
"""

from unittest.mock import Mock, patch

from reforge.runtime.orchestration.governor import ClassifyStage, ExecutionGovernor, RuntimeContext
from reforge.runtime.orchestration.graph.nodes.reflection import reflection_node
from reforge.runtime.orchestration.outcome_resolver import TaskOutcome, resolve_outcome
from reforge.runtime.policy.retry_policy import RetryPolicy
from reforge.runtime.policy.task_intent import TaskIntent
from reforge.runtime.domain.state.models import (
    ExecutionState,
    RuntimeControlState,
    RuntimeState,
    SemanticState,
)

_KEYERROR_SIG = {
    "error_class": "KeyError",
    "error_type": "KeyError",
    "execution_phase": "runtime",
    "domain": "pandas",
    "root_cause": "missing_key",
    "missing_key": "revenue",
}

_FILE_SIG = {
    "error_class": "FileNotFoundError",
    "error_type": "FileNotFoundError",
    "execution_phase": "runtime",
    "domain": "filesystem",
    "root_cause": "missing_file",
    "missing_file": "data.csv",
}


def _failing_ctx(history: list[dict], task_intent: str = "NORMAL_EXECUTION") -> RuntimeContext:
    ctx = RuntimeContext(
        state=RuntimeState(
            user_request="analyze sales",
            exec_state=ExecutionState(exit_code=1, stderr="KeyError: 'revenue'"),
            semantic_state=SemanticState(failure_signature_history=history),
        ),
        request="analyze sales",
    )
    ctx.task_intent = task_intent
    return ctx


class TestClassifyStageDetector:
    def test_two_identical_signatures_flip_to_repeated_signature(self):
        ctx = ClassifyStage().execute(_failing_ctx([_KEYERROR_SIG, dict(_KEYERROR_SIG)]))
        assert ctx.classification.failure_mode == "repeated_signature"
        assert not ctx.classification.retryable
        assert not ctx.classification.is_expected_failure

    def test_flip_skips_memory_recall(self):
        recall = Mock(return_value=[])
        with patch("reforge.memory.execution_memory.ExecutionMemory.recall_similar", recall):
            ClassifyStage().execute(_failing_ctx([_KEYERROR_SIG, dict(_KEYERROR_SIG)]))
        recall.assert_not_called()

    def test_differing_signatures_stay_retryable(self):
        with patch(
            "reforge.memory.execution_memory.ExecutionMemory.recall_similar", return_value=[]
        ):
            ctx = ClassifyStage().execute(_failing_ctx([_FILE_SIG, _KEYERROR_SIG]))
        assert ctx.classification.failure_mode == "execution_error"
        assert ctx.classification.retryable

    def test_single_failure_stays_retryable(self):
        with patch(
            "reforge.memory.execution_memory.ExecutionMemory.recall_similar", return_value=[]
        ):
            ctx = ClassifyStage().execute(_failing_ctx([_KEYERROR_SIG]))
        assert ctx.classification.retryable

    def test_empty_error_class_never_matches(self):
        empty = {"error_class": "", "root_cause": "unknown"}
        with patch(
            "reforge.memory.execution_memory.ExecutionMemory.recall_similar", return_value=[]
        ):
            ctx = ClassifyStage().execute(_failing_ctx([dict(empty), dict(empty)]))
        assert ctx.classification.failure_mode == "execution_error"
        assert ctx.classification.retryable

    def test_expected_failure_intent_is_exempt(self):
        # RECOVERABLE_DEMO's stated intent is that the failure IS recoverable;
        # the history signal must not overrule an explicit intent.
        ctx = ClassifyStage().execute(
            _failing_ctx([_KEYERROR_SIG, dict(_KEYERROR_SIG)], task_intent="RECOVERABLE_DEMO")
        )
        assert ctx.classification.failure_mode == "recoverable_intentional"
        assert ctx.classification.retryable


class TestRetryPolicyBranch:
    def test_repeated_signature_stops_with_budget_remaining(self):
        decision = RetryPolicy().decide(
            classification={
                "is_expected_failure": False,
                "retryable": False,
                "failure_mode": "repeated_signature",
            },
            execution=None,
            evaluation=None,
            retry_count=1,
            max_retries=2,
        )
        assert decision.action.value == "STOP"
        assert decision.reason == "repeated_failure_signature"


class TestOutcomeResolution:
    def test_deliberate_stop_is_not_reported_as_retries_exhausted(self):
        outcome, reason = resolve_outcome(
            task_intent="NORMAL_EXECUTION",
            execution_exit_code=1,
            retry_count=1,
            eval_passed=False,
            policy_action="STOP",
            policy_reason="repeated_failure_signature",
        )
        assert outcome == TaskOutcome.FAILED
        assert reason == "repeated_failure_signature"

    def test_budget_exhaustion_reason_unchanged(self):
        outcome, reason = resolve_outcome(
            task_intent="NORMAL_EXECUTION",
            execution_exit_code=1,
            retry_count=2,
            eval_passed=False,
            policy_action="STOP",
            policy_reason="retry_limit_reached_with_error",
        )
        assert outcome == TaskOutcome.FAILED
        assert reason == "retries_exhausted"


class TestReflectionHistoryAppend:
    def test_failed_attempts_accumulate_signatures(self):
        # exit_code != 0 with no traceback takes the NonZeroExit path — no LLM.
        state = RuntimeState(
            user_request="query the db",
            exec_state=ExecutionState(exit_code=1, stdout="error: no such table"),
        )
        first = reflection_node(state)
        history1 = first["semantic_state"].failure_signature_history
        assert len(history1) == 1
        assert history1[0]["error_class"] == "NonZeroExit"

        state2 = state.model_copy(update={"semantic_state": first["semantic_state"]})
        second = reflection_node(state2)
        history2 = second["semantic_state"].failure_signature_history
        assert len(history2) == 2
        assert history2[0] == history2[1]

    def test_success_does_not_append(self):
        state = RuntimeState(
            user_request="query the db",
            exec_state=ExecutionState(exit_code=0, stdout="42"),
        )
        result = reflection_node(state)
        assert result["semantic_state"].failure_signature_history == []


class TestGovernorEndToEnd:
    def test_repeated_signature_resolves_to_deliberate_stop(self):
        state = RuntimeState(
            user_request="analyze sales",
            exec_state=ExecutionState(exit_code=1, stderr="KeyError: 'revenue'"),
            control_state=RuntimeControlState(retry_count=1),
            semantic_state=SemanticState(
                task_intent="NORMAL_EXECUTION",
                failure_signature_history=[_KEYERROR_SIG, dict(_KEYERROR_SIG)],
            ),
        )
        mock = Mock(return_value=TaskIntent.NORMAL_EXECUTION)
        with patch(
            "reforge.runtime.orchestration.governor.intent_stage.classify_intent", mock
        ):
            r = ExecutionGovernor(max_retries=2).resolve(state)
        assert r.action == "STOP"
        assert r.outcome == "FAILED"
        assert r.reason == "repeated_failure_signature"
        assert r.failure_mode == "repeated_signature"
        assert not r.retryable
        mock.assert_not_called()  # intent cached on semantic_state

    def test_first_identical_failure_still_retries(self):
        state = RuntimeState(
            user_request="analyze sales",
            exec_state=ExecutionState(exit_code=1, stderr="KeyError: 'revenue'"),
            control_state=RuntimeControlState(retry_count=0),
            semantic_state=SemanticState(
                task_intent="NORMAL_EXECUTION",
                failure_signature_history=[_KEYERROR_SIG],
            ),
        )
        with patch(
            "reforge.memory.execution_memory.ExecutionMemory.recall_similar", return_value=[]
        ):
            r = ExecutionGovernor(max_retries=2).resolve(state)
        assert r.action == "RETRY"
