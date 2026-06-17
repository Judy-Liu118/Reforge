"""Decomposition models — multi-step task planning and result aggregation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from reforge.runtime.domain.state.models import RuntimeState


class SubtaskPlan(BaseModel):
    """One subtask extracted from a multi-step request."""

    index: int = Field(default=0)
    request: str = Field(default="")
    description: str = Field(default="")
    depends_on: list[int] = Field(default_factory=list)


class DecompositionResult(BaseModel):
    """Output of TaskDecomposer — single-task or ordered subtask list."""

    is_multistep: bool = Field(default=False)
    subtasks: list[SubtaskPlan] = Field(default_factory=list)
    reasoning: str = Field(default="")
    original_request: str = Field(default="")

    @classmethod
    def single(cls, user_request: str) -> "DecompositionResult":
        """Passthrough: the request is a single atomic task."""
        return cls(
            is_multistep=False,
            subtasks=[SubtaskPlan(index=0, request=user_request, description="")],
            original_request=user_request,
        )


class SubtaskResult(BaseModel):
    """Result of one subtask execution."""

    subtask: SubtaskPlan = Field(default_factory=SubtaskPlan)
    task_outcome: str = Field(default="")
    final_answer: str = Field(default="")
    retry_count: int = Field(default=0)
    duration_ms: float = Field(default=0.0)
    session_id: str = Field(default="")


class SubtaskRuntimeState(BaseModel):
    """Full lifecycle record for one subtask — includes the complete RuntimeState.

    Use this when you need retry history, evaluation results, event tracing, or
    memory write decisions per subtask.  SubtaskResult is a lightweight summary
    derived from this.
    """

    subtask: SubtaskPlan = Field(default_factory=SubtaskPlan)
    session_id: str = Field(default="")
    state: object = Field(default=None)  # RuntimeState at runtime (avoid circular import)
    duration_ms: float = Field(default=0.0)
    error: str = Field(default="")  # Populated when parallel execution raised before state was produced

    def to_result(self) -> SubtaskResult:
        """Derive a lightweight SubtaskResult from the full state."""
        s = self.state
        if s is None or not hasattr(s, "outcome_state"):
            return SubtaskResult(
                subtask=self.subtask,
                task_outcome="FAILED",
                final_answer=self.error,
                retry_count=0,
                duration_ms=self.duration_ms,
                session_id=self.session_id,
            )
        return SubtaskResult(
            subtask=self.subtask,
            task_outcome=s.outcome_state.task_outcome or "FAILED",
            final_answer=s.outcome_state.final_answer or "",
            retry_count=s.control_state.retry_count,
            duration_ms=self.duration_ms,
            session_id=self.session_id,
        )


class MultiStepResult(BaseModel):
    """Aggregated result of a multi-step session."""

    original_request: str = Field(default="")
    subtask_results: list[SubtaskResult] = Field(default_factory=list)
    overall_outcome: str = Field(default="")   # COMPLETE / PARTIAL / FAILED
    final_answer: str = Field(default="")
    total_duration_ms: float = Field(default=0.0)

    @classmethod
    def from_results(
        cls,
        original_request: str,
        results: list[SubtaskResult],
    ) -> "MultiStepResult":
        total_ms = sum(r.duration_ms for r in results)
        answers = [r.final_answer for r in results if r.final_answer]
        final = "\n\n".join(
            f"[Step {r.subtask.index + 1}] {r.final_answer}" for r in results if r.final_answer
        )
        success_outcomes = {"SUCCESS", "RECOVERED", "EXPECTED_FAILURE"}
        all_ok = all(r.task_outcome in success_outcomes for r in results)
        any_ok = any(r.task_outcome in success_outcomes for r in results)
        overall = "COMPLETE" if all_ok else ("PARTIAL" if any_ok else "FAILED")
        return cls(
            original_request=original_request,
            subtask_results=results,
            overall_outcome=overall,
            final_answer=final,
            total_duration_ms=total_ms,
        )
