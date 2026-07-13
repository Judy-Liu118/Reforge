from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from reforge.runtime.classification.models import FailureClassification


# Sentinel exit_code that sandbox backends MUST emit on timeout.
# Negative so it can never collide with a real OS exit code (0..255).
TIMEOUT_EXIT_CODE: int = -1


class ExecutionOutput(BaseModel):
    """Sandbox execution result — produced by the sandbox executor.

    Exists alongside ExecutionState (below) on purpose, despite the
    overlapping fields:

    - ExecutionOutput is the *backend contract*: every sandbox backend
      (subprocess, docker, …) returns one of these from execute(). Its
      exit_code is `int` (never None) because the value is only ever
      constructed after a real run completes.

    - ExecutionState is the *RuntimeState slot*: stored on the live state,
      mutated across nodes. Its exit_code is `int | None` because "not yet
      executed" is a meaningful state before the first run.

    `RuntimeState.execution_output` is a property that projects
    ExecutionState → ExecutionOutput when an exit_code is present, so
    classifier/policy can take `ExecutionOutput | None` and let the type
    system distinguish "no run yet" from "ran with exit_code=0".

    `exit_code == TIMEOUT_EXIT_CODE` signals the run was killed by the
    backend's timeout watchdog; classifier/resolver branch on this sentinel.
    """

    stdout: str = Field(default="")
    stderr: str = Field(default="")
    exit_code: int = Field(default=0)
    duration_ms: float = Field(default=0.0)


class ReflectionResult(BaseModel):
    """Reflection on a failed execution — root cause analysis only.

    Does NOT classify intent or decide retry. That is the classifier's job.
    """

    error_type: str = Field(default="")
    error_summary: str = Field(default="")
    suggested_fix: str = Field(default="")


class AttemptRecord(BaseModel):
    """Metadata for a single execution attempt, including evaluation outcome."""

    attempt: int = Field(default=0)
    exit_code: int = Field(default=0)
    duration_ms: float = Field(default=0.0)
    error_type: str = Field(default="")
    eval_score: float = Field(default=1.0)
    eval_failure_type: str = Field(default="")


class EvalCheck(BaseModel):
    """A single heuristic check result."""

    name: str = Field(default="")
    passed: bool = Field(default=True)
    detail: str = Field(default="")


class EvaluationResult(BaseModel):
    """Result of heuristic evaluation on execution output.
    Evaluator = signal provider only. Runtime Decision = final authority.
    """

    passed: bool = Field(default=True)
    score: float = Field(default=1.0)
    checks: list[EvalCheck] = Field(default_factory=list)
    summary: str = Field(default="")
    failure_type: str = Field(default="")


class TaskRequirements(BaseModel):
    """Process constraints extracted from the user's request.

    Some tasks require a specific execution sequence, not just a final outcome.
    e.g., "故意加乱码让语法出错" → must fail first, then recover.
    e.g., "演示 traceback" → expects uncaught exception, not try/except.
    """

    must_fail_first: bool = Field(default=False)
    requires_recovery: bool = Field(default=False)
    expected_final_success: bool = Field(default=True)
    expects_uncaught_exception: bool = Field(default=False)


class RuntimeDecisionAction(str, Enum):
    RETRY = "RETRY"
    STOP = "STOP"
    ACCEPT = "ACCEPT"


class RuntimeDecision(BaseModel):
    """Unified policy decision — produced by RetryPolicy, consumed by workflow and CLI."""

    action: RuntimeDecisionAction = Field(default=RuntimeDecisionAction.ACCEPT)
    reason: str = Field(default="")
    retryable: bool = Field(default=False)
    terminal: bool = Field(default=False)

    @classmethod
    def retry(cls, reason: str) -> "RuntimeDecision":
        return cls(action=RuntimeDecisionAction.RETRY, reason=reason, retryable=True, terminal=False)

    @classmethod
    def stop(cls, reason: str, terminal: bool = True) -> "RuntimeDecision":
        return cls(action=RuntimeDecisionAction.STOP, reason=reason, retryable=False, terminal=terminal)

    @classmethod
    def accept(cls, reason: str) -> "RuntimeDecision":
        return cls(action=RuntimeDecisionAction.ACCEPT, reason=reason, retryable=False, terminal=False)


class ExecutionState(BaseModel):
    """Owner: executor node. Mutable execution slot on RuntimeState.

    Mirrors ExecutionOutput's fields but allows None for exit_code /
    duration_ms so the pre-execution state ("planner ran, executor
    hasn't") has a well-typed representation. See ExecutionOutput's
    docstring for the contract split.
    """

    stdout: str = Field(default="")
    stderr: str = Field(default="")
    exit_code: int | None = Field(default=None)
    duration_ms: float | None = Field(default=None)


class RuntimeControlState(BaseModel):
    """Owner: policy engine / governor. Retry and capability decisions."""

    retry_count: int = Field(default=0)
    retry_decision_action: str | None = Field(default=None)
    capability_result: str | None = Field(default=None)
    policy_reason: str | None = Field(default=None)


class FailureSnapshot(BaseModel):
    """Snapshot of the most recent *failed* attempt — survives later successes.

    Written by the reflection node whenever an attempt fails; never cleared.
    RuntimeState only keeps the latest execution result, so by the time a
    RECOVERED session reaches final_response the failing traceback is gone.
    This snapshot is what lets the memory write-back pair the failure's
    structural signature with the repair that ended up working.
    """

    error_type: str = Field(default="")
    suggested_fix: str = Field(default="")
    failure_mode: str = Field(default="")
    problem_signature: dict = Field(default_factory=dict)


class SemanticState(BaseModel):
    """Owner: reflection / evaluator / intent classifier. Hints only, no authority."""

    task_intent: str | None = Field(default=None)
    plan: str | None = Field(default=None)
    reflection_summary: str | None = Field(default=None)
    evaluation_summary: str | None = Field(default=None)
    reflection_result: Optional[ReflectionResult] = Field(default=None)
    evaluation_result: Optional[EvaluationResult] = Field(default=None)
    # Set by retry_decision_node from the governor's RuntimeResolution; consumed
    # by RetryContextData/build_retry_prompt so the next codegen attempt sees
    # the memory-recalled repair strategy. Cleared (None) when the governor
    # produced no hint, so a stale hint never leaks into a later attempt.
    repair_hint: str | None = Field(default=None)
    last_failure: Optional[FailureSnapshot] = Field(default=None)
    # Appended by the reflection node on every failed attempt (one signature
    # per attempt, in order; never cleared). ClassifyStage reads the tail to
    # detect the same structural failure recurring across consecutive
    # attempts — the history-based unrecoverability detector (L3).
    failure_signature_history: list[dict] = Field(default_factory=list)


class OutcomeState(BaseModel):
    """Owner: governor / outcome resolver. Final task result."""

    task_outcome: str | None = Field(default=None)
    outcome_reason: str | None = Field(default=None)
    final_answer: str | None = Field(default=None)


class RuntimeState(BaseModel):
    """Typed runtime state for the self-healing loop.

    Canonical write path: exec_state, control_state, semantic_state, outcome_state.
    execution_output and traceback are read-only properties derived from exec_state.
    semantic_state owns: reflection_result, evaluation_result, task_intent, summaries.
    Top-level payloads: user_request, generated_code, attempts, task_requirements,
    capability_decision, classification_result.
    """

    # --- Input / output payloads ---
    user_request: str = Field(default="")
    generated_code: str = Field(default="")
    attempts: list[AttemptRecord] = Field(default_factory=list)
    task_requirements: Optional[TaskRequirements] = Field(default=None)
    capability_decision: Optional[dict] = Field(default=None)
    classification_result: Optional[FailureClassification] = Field(default=None)
    # Task-level immutable: written only at session construction by RuntimeRunner.
    # Mid-loop append is reserved for future screenshot capture but is intentionally
    # not wired — any node return dict that mutates this is caught by the chunk-loop
    # invariant in RuntimeRunner.stream and raised as a hard error.
    image_inputs: list[str] = Field(default_factory=list)

    # --- Nested sub-states — canonical for ownership-tracked fields ---
    exec_state: ExecutionState = Field(default_factory=ExecutionState)
    control_state: RuntimeControlState = Field(default_factory=RuntimeControlState)
    semantic_state: SemanticState = Field(default_factory=SemanticState)
    outcome_state: OutcomeState = Field(default_factory=OutcomeState)

    # --- Derived read-only views of exec_state ---

    @property
    def execution_output(self) -> Optional[ExecutionOutput]:
        """Derived from exec_state. Returns None before first execution."""
        if self.exec_state.exit_code is None:
            return None
        return ExecutionOutput(
            stdout=self.exec_state.stdout,
            stderr=self.exec_state.stderr,
            exit_code=self.exec_state.exit_code,
            duration_ms=self.exec_state.duration_ms or 0.0,
        )

    @property
    def traceback(self) -> str:
        """Derived from exec_state. Non-empty only when exit_code != 0."""
        if self.exec_state.exit_code is not None and self.exec_state.exit_code != 0:
            return self.exec_state.stderr
        return ""
