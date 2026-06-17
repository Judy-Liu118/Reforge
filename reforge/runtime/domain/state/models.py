from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ExecutionOutput(BaseModel):
    """Sandbox execution result — produced by the sandbox executor."""

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


class RetryContext(BaseModel):
    """Structured context for retry generation — prevents retry drift."""

    original_intent: str = Field(default="")
    previous_failure_reason: str = Field(default="")
    retry_objective: str = Field(default="")


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


class ExecutionPolicy(BaseModel):
    """Runtime execution policy — configurable per-session constraints."""

    max_retries: int = Field(default=2)
    timeout_sec: int = Field(default=10)
    allow_network: bool = Field(default=False)


class DecisionReason(str, Enum):
    CLEAN_EXECUTION = "clean_execution"
    INTENTIONAL_FAILURE_ACCEPTED = "intentional_failure_accepted"
    TASK_FIDELITY_ACHIEVED = "task_fidelity_achieved"
    EXECUTION_RECOVERED = "execution_recovered"
    TASK_OBJECTIVE_SATISFIED = "task_objective_satisfied"
    EVALUATION_FAILED = "evaluation_failed"
    RETRY_LIMIT_REACHED = "retry_limit_reached"
    SILENT_FAILURE = "silent_failure"


class ExecutionState(BaseModel):
    """Owner: executor node. Deterministic execution results only."""

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


class SemanticState(BaseModel):
    """Owner: reflection / evaluator / intent classifier. Hints only, no authority."""

    task_intent: str | None = Field(default=None)
    reflection_summary: str | None = Field(default=None)
    evaluation_summary: str | None = Field(default=None)
    reflection_result: Optional[ReflectionResult] = Field(default=None)
    evaluation_result: Optional[EvaluationResult] = Field(default=None)


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
    classification_result: Optional[dict] = Field(default=None)

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
