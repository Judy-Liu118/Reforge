"""ClassifyStage — failure classification (intentional/retryable/failure_mode) + memory recall."""

from __future__ import annotations

from reforge.memory.execution_memory import ExecutionMemory
from reforge.memory.fingerprint import extract_fingerprint
from reforge.runtime.classification.classifier import FailureClassifier
from reforge.runtime.orchestration.governor.stages import RuntimeContext
from reforge.runtime.infrastructure.trajectory.store import TrajectoryStore

# Minimum number of historical sessions with the same eval failure_type
# before we inject a pattern-warning into the retry hint.
_PATTERN_THRESHOLD = 2

# Consecutive attempts within THIS session that must share an identical
# structural fingerprint before the run is declared unrecoverable (L3).
# With the default budget of max_retries=2 (3 attempts total), 2 is the
# only value that fires before budget exhaustion — it trades the third
# attempt away when the second failed exactly like the first.
_REPEAT_SIGNATURE_THRESHOLD = 2


def _is_repeated_signature(history: list[dict]) -> bool:
    """True when the last _REPEAT_SIGNATURE_THRESHOLD failures share one
    non-empty structural fingerprint (full-dict equality — same error class
    AND same target module/key/file/name, not just the same exception type)."""
    if len(history) < _REPEAT_SIGNATURE_THRESHOLD:
        return False
    recent = history[-_REPEAT_SIGNATURE_THRESHOLD:]
    if not recent[0].get("error_class"):
        return False
    return all(sig == recent[0] for sig in recent[1:])


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
        ctx.classification = self._classifier.classify(
            task_intent=ctx.task_intent,
            execution=execution_output,
            evaluation=evaluation_result,
        )

        # History-based unrecoverability (L3): the same structural fingerprint
        # on consecutive attempts means retrying is burning budget on a failure
        # the codegen cannot route around — flip to a deliberate STOP before
        # the limit. Expected failures are exempt: RECOVERABLE_DEMO's stated
        # intent is that the failure IS recoverable, which this signal cannot
        # overrule.
        if (
            ctx.classification.retryable
            and not ctx.classification.is_expected_failure
            and _is_repeated_signature(ctx.state.semantic_state.failure_signature_history)
        ):
            ctx.classification = ctx.classification.model_copy(
                update={
                    "retryable": False,
                    "failure_mode": "repeated_signature",
                    "severity": "high",
                }
            )
            return ctx

        # Recall past repairs for this failure mode → forwarded as repair_hint,
        # NOT outcome_reason (which PolicyStage owns and will overwrite).
        # The current failure's structural fingerprint drives the recall
        # scoring (error_class / root_cause / domain matches) — without it,
        # recall degrades to coarse failure_mode + word overlap.
        if ctx.classification.retryable and ctx.classification.failure_mode not in ("none", ""):
            snapshot = ctx.state.semantic_state.last_failure
            if snapshot is not None and snapshot.problem_signature:
                signature = snapshot.problem_signature
            else:
                stderr = execution_output.stderr if execution_output else ""
                signature = extract_fingerprint(stderr).to_dict()
            records = self._memory.recall_similar(
                ctx.request,
                ctx.classification.failure_mode,
                problem_signature=signature,
            )
            if records and records[0].repair_strategy:
                ctx.repair_hint = records[0].repair_strategy

        # Inject warning when this evaluation failure_type recurred in past sessions.
        # Treated as a boolean signal — the threshold gates inclusion, not magnitude.
        if (
            ctx.classification.retryable
            and evaluation_result
            and not evaluation_result.passed
            and evaluation_result.failure_type
        ):
            count = self._trajectories.count_by_eval_pattern(
                failure_type=evaluation_result.failure_type,
            )
            if count >= _PATTERN_THRESHOLD:
                pattern_hint = f"[recurring failure: {evaluation_result.failure_type}] "
                ctx.repair_hint = (pattern_hint + (ctx.repair_hint or "")).strip()

        return ctx
