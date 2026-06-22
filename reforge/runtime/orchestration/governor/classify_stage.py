"""ClassifyStage — failure classification (intentional/retryable/failure_mode) + memory recall."""

from __future__ import annotations

from reforge.memory.execution_memory import ExecutionMemory
from reforge.runtime.classification.classifier import FailureClassifier
from reforge.runtime.orchestration.governor.stages import RuntimeContext
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore

# Minimum number of historical sessions with the same eval failure_type
# before we inject a pattern-warning into the retry hint.
_PATTERN_THRESHOLD = 2


class ClassifyStage:
    """Stage 3: Classify failure + recall similar past experiences.

    Also queries TrajectoryStore for recurring evaluation failure patterns.
    When the same eval failure_type appears ≥ _PATTERN_THRESHOLD times in
    similar historical sessions, a targeted warning is prepended to the
    repair hint, helping the retry prompt avoid the known pitfall.
    """

    def __init__(self, trajectory_store: TrajectoryStore | None = None) -> None:
        self._classifier = FailureClassifier()
        self._memory = ExecutionMemory()
        self._trajectories = trajectory_store or TrajectoryStore()

    def execute(self, ctx: RuntimeContext) -> RuntimeContext:
        execution_output = ctx.state.execution_output
        evaluation_result = ctx.state.semantic_state.evaluation_result
        classification = self._classifier.classify(
            task_intent=ctx.task_intent,
            execution=execution_output,
            evaluation=evaluation_result,
            retry_count=ctx.state.control_state.retry_count,
        )
        ctx.intentional = classification.intentional
        ctx.retryable = classification.retryable
        ctx.failure_mode = classification.failure_mode

        # Recall past repairs for this failure mode
        if ctx.retryable and ctx.failure_mode not in ("none", ""):
            records = self._memory.recall_similar(ctx.request, ctx.failure_mode)
            if records and records[0].repair_strategy:
                ctx.outcome_reason = records[0].repair_strategy

        # Inject warning when this evaluation failure_type recurred in past sessions.
        if (
            ctx.retryable
            and evaluation_result
            and not evaluation_result.passed
            and evaluation_result.failure_type
        ):
            similar = self._trajectories.find_by_eval_pattern(
                failure_type=evaluation_result.failure_type,
                limit=_PATTERN_THRESHOLD + 1,
            )
            if len(similar) >= _PATTERN_THRESHOLD:
                pattern_hint = (
                    f"[recurring:{evaluation_result.failure_type} "
                    f"seen {len(similar)} times] "
                )
                ctx.outcome_reason = (pattern_hint + (ctx.outcome_reason or "")).strip()

        return ctx
