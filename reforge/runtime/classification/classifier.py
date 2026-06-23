"""FailureClassifier — classifies failures into FailureClassification.

Consumes: task_intent, execution result, evaluation result.
Note: does NOT consume reflection. Reflection = debugging hints only, no runtime authority.
"""

from __future__ import annotations

from reforge.runtime.classification.models import FailureClassification
from reforge.runtime.domain.state.models import (
    EvaluationResult,
    ExecutionOutput,
    TIMEOUT_EXIT_CODE,
)


class FailureClassifier:
    """Classify a runtime failure into structured FailureClassification.

    Deterministic signals only — task_intent, exit_code, evaluation.
    Reflection is explicitly excluded from classification to maintain boundary.
    """

    def classify(
        self,
        task_intent: str,
        execution: ExecutionOutput | None,
        evaluation: EvaluationResult | None,
    ) -> FailureClassification:
        # --- No failure at all ---
        if execution and execution.exit_code == 0 and (evaluation is None or evaluation.passed):
            return FailureClassification(
                intentional=False, retryable=False,
                failure_mode="none", severity="low", confidence=1.0,
            )

        # --- Timeout detection ---
        if execution and execution.exit_code == TIMEOUT_EXIT_CODE:
            return FailureClassification(
                intentional=False, retryable=False,
                failure_mode="timeout", severity="high", confidence=0.9,
            )

        # --- STRESS_TEST / SANDBOX_ESCAPE → never retry ---
        if task_intent in ("STRESS_TEST", "SANDBOX_ESCAPE"):
            return FailureClassification(
                intentional=False, retryable=False,
                failure_mode=task_intent.lower(), severity="high", confidence=1.0,
            )

        # --- EXPECTED_ERROR / TRACEBACK_DEMO → intentional terminal ---
        if task_intent in ("EXPECTED_ERROR", "TRACEBACK_DEMO"):
            return FailureClassification(
                intentional=True, retryable=False,
                failure_mode="terminal_intentional", severity="low", confidence=1.0,
            )

        # --- RECOVERABLE_DEMO → intentional recoverable ---
        if task_intent == "RECOVERABLE_DEMO":
            return FailureClassification(
                intentional=True, retryable=True,
                failure_mode="recoverable_intentional", severity="low", confidence=1.0,
            )

        # --- NORMAL_EXECUTION with error → retryable ---
        if execution and execution.exit_code != 0:
            return FailureClassification(
                intentional=False, retryable=True,
                failure_mode="execution_error", severity="medium", confidence=0.9,
            )

        # --- Evaluation failure → retryable ---
        if evaluation and not evaluation.passed:
            return FailureClassification(
                intentional=False, retryable=True,
                failure_mode="evaluation_failure", severity="medium", confidence=0.7,
            )

        return FailureClassification(
            intentional=False, retryable=False,
            failure_mode="unknown", severity="low", confidence=0.5,
        )
