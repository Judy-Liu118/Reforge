"""RetryPolicy — pure function, no LangGraph dependency. Consumes FailureClassification, produces RuntimeDecision."""

from __future__ import annotations

from reforge.runtime.policy.decision import RuntimeDecision
from reforge.runtime.domain.state.models import (
    EvaluationResult,
    ExecutionOutput,
)


class RetryPolicy:
    """Decide RETRY / STOP / ACCEPT based on classification + execution + retry count.

    Pure Python — no LangGraph, no side effects.
    Only consumes FailureClassification (not reflection directly).
    """

    def decide(
        self,
        classification: dict,
        execution: ExecutionOutput | None,
        evaluation: EvaluationResult | None,
        retry_count: int,
        max_retries: int = 2,
    ) -> RuntimeDecision:
        is_expected_failure = classification.get("is_expected_failure", False)
        retryable = classification.get("retryable", False)
        failure_mode = classification.get("failure_mode", "")

        if is_expected_failure and not retryable:
            return RuntimeDecision.stop(reason="terminal_intentional_failure")

        if failure_mode == "timeout":
            return RuntimeDecision.stop(reason="timeout")

        # ClassifyStage's history detector (L3): identical structural
        # fingerprint on consecutive attempts — deliberate STOP with budget
        # remaining rather than burning the rest of it on the same failure.
        if failure_mode == "repeated_signature":
            return RuntimeDecision.stop(reason="repeated_failure_signature")

        if retry_count >= max_retries:
            if execution and execution.exit_code != 0:
                return RuntimeDecision.stop(reason="retry_limit_reached_with_error")
            if evaluation and not evaluation.passed:
                return RuntimeDecision.stop(reason="retry_limit_reached_on_eval_fail")
            return RuntimeDecision.stop(reason="retry_limit_reached")

        if retryable:
            return RuntimeDecision.retry(reason=failure_mode)

        if execution and execution.exit_code != 0:
            return RuntimeDecision.retry(reason="execution_error")

        if evaluation and not evaluation.passed:
            return RuntimeDecision.retry(reason="evaluation_failed")

        return RuntimeDecision.accept(reason="execution_accepted")
