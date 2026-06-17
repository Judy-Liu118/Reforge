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
        intentional = classification.get("intentional", False)
        retryable = classification.get("retryable", False)
        failure_mode = classification.get("failure_mode", "")

        # --- Terminal intentional → STOP ---
        if intentional and not retryable:
            return RuntimeDecision.stop(reason="terminal_intentional_failure")

        # --- Timeout → STOP ---
        if failure_mode == "timeout":
            return RuntimeDecision.stop(reason="timeout")

        # --- Retry limit reached → STOP ---
        if retry_count >= max_retries:
            if execution and execution.exit_code != 0:
                return RuntimeDecision.stop(reason="retry_limit_reached_with_error")
            if evaluation and not evaluation.passed:
                return RuntimeDecision.stop(reason="retry_limit_reached_on_eval_fail")
            return RuntimeDecision.stop(reason="retry_limit_reached")

        # --- Retryable → RETRY ---
        if retryable:
            return RuntimeDecision.retry(reason=failure_mode)

        # --- Execution error (not classified as retryable) → RETRY ---
        if execution and execution.exit_code != 0:
            return RuntimeDecision.retry(reason="execution_error")

        # --- Evaluation failure → RETRY ---
        if evaluation and not evaluation.passed:
            return RuntimeDecision.retry(reason="evaluation_failed")

        # --- Clean → ACCEPT ---
        return RuntimeDecision.accept(reason="execution_accepted")
