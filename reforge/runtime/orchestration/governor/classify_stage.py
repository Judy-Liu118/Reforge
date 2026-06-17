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
        eo = ctx.state.execution_output
        er = ctx.state.semantic_state.evaluation_result
        clf = self._classifier.classify(
            task_intent=ctx.task_intent,
            execution=eo,
            evaluation=er,
            retry_count=ctx.state.control_state.retry_count,
        )
        ctx.intentional = clf.intentional
        ctx.retryable = clf.retryable
        ctx.failure_mode = clf.failure_mode

        # Recall past repairs for this failure mode
        if ctx.retryable and ctx.failure_mode not in ("none", ""):
            records = self._memory.recall_similar(ctx.request, ctx.failure_mode)
            if records and records[0].repair_strategy:
                ctx.outcome_reason = records[0].repair_strategy

        # Evaluation pattern learning — inject warning for recurring eval failures
        if ctx.retryable and er and not er.passed and er.failure_type:
            similar = self._trajectories.find_by_eval_pattern(
                failure_type=er.failure_type,
                limit=_PATTERN_THRESHOLD + 1,
            )
            if len(similar) >= _PATTERN_THRESHOLD:
                pattern_hint = (
                    f"[recurring:{er.failure_type} seen {len(similar)} times] "
                )
                ctx.outcome_reason = (pattern_hint + (ctx.outcome_reason or "")).strip()

        return ctx
