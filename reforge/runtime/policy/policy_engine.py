"""PolicyEngine — thin integration layer. Only consumes classification.

No LangGraph dependency. Pure Python adaptation between state models and policy.
"""

from __future__ import annotations

from reforge.runtime.policy.decision import RuntimeDecision
from reforge.runtime.policy.retry_policy import RetryPolicy
from reforge.runtime.domain.state.models import RuntimeState


class PolicyEngine:
    """Unified runtime policy entry point. Wraps RetryPolicy.

    Only consumes FailureClassification from the classifier.
    Does NOT re-analyze traceback or do its own reflection.
    """

    def __init__(self, max_retries: int = 2) -> None:
        self._max_retries = max_retries
        self._policy = RetryPolicy()

    def evaluate(self, state: RuntimeState, classification: dict | None = None) -> RuntimeDecision:
        """Evaluate current runtime state via classification → policy decision."""
        clf = classification or state.classification_result or {}
        return self._policy.decide(
            classification=clf,
            execution=state.execution_output,
            evaluation=state.semantic_state.evaluation_result,
            retry_count=state.control_state.retry_count,
            max_retries=self._max_retries,
        )
