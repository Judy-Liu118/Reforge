"""Trajectory models — semantic arc of a single execution session.

Distinct from SessionRecord (bare metadata) and TraceEvent (observability).
TrajectoryRecord captures the semantic content across attempts for future
planning recall: what failed, how it was reflected on, how it recovered.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from reforge.runtime.domain.state.models import RuntimeState


class AttemptStep(BaseModel):
    """Semantic content of one execution attempt."""

    attempt: int = Field(default=0)
    generated_code_hash: str = Field(default="")   # SHA1[:12], not full code
    exit_code: int = Field(default=0)
    error_type: str = Field(default="")
    reflection_summary: str = Field(default="")
    suggested_fix: str = Field(default="")
    duration_ms: float = Field(default=0.0)
    eval_score: float = Field(default=1.0)
    eval_failure_type: str = Field(default="")


class TrajectoryRecord(BaseModel):
    """Complete semantic arc of one session: intent → attempts → resolution.

    recovery_chain captures the sequence of error_types across attempts,
    enabling future planners to recognize recurring failure patterns.
    """

    trajectory_id: str = Field(default="")
    session_id: str = Field(default="")
    timestamp: str = Field(default="")
    user_request: str = Field(default="")
    task_intent: str = Field(default="")
    total_attempts: int = Field(default=0)
    final_outcome: str = Field(default="")
    outcome_reason: str = Field(default="")
    steps: list[AttemptStep] = Field(default_factory=list)
    problem_signature: dict = Field(default_factory=dict)
    recovery_chain: list[str] = Field(default_factory=list)

    @classmethod
    def from_final_state(cls, state: "RuntimeState", session_id: str) -> "TrajectoryRecord":
        """Build a TrajectoryRecord from a terminal RuntimeState."""
        steps: list[AttemptStep] = []
        rr = state.semantic_state.reflection_result
        er = state.semantic_state.evaluation_result
        last_attempt_idx = len(state.attempts) - 1

        for i, a in enumerate(state.attempts):
            code_hash = (
                hashlib.sha1(state.generated_code.encode()).hexdigest()[:12]
                if state.generated_code else ""
            )
            # Evaluation result is available only for the final attempt
            is_last = (i == last_attempt_idx)
            steps.append(AttemptStep(
                attempt=a.attempt,
                generated_code_hash=code_hash,
                exit_code=a.exit_code,
                error_type=a.error_type,
                reflection_summary=rr.error_summary if rr else "",
                suggested_fix=rr.suggested_fix if rr else "",
                duration_ms=a.duration_ms,
                eval_score=er.score if (is_last and er) else 1.0,
                eval_failure_type=er.failure_type if (is_last and er) else "",
            ))

        recovery_chain = [s.error_type for s in steps if s.error_type]

        sig = _build_signature(state)

        return cls(
            trajectory_id=uuid.uuid4().hex[:12],
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_request=state.user_request,
            task_intent=state.semantic_state.task_intent,
            total_attempts=len(state.attempts),
            final_outcome=state.outcome_state.task_outcome,
            outcome_reason=state.outcome_state.outcome_reason,
            steps=steps,
            problem_signature=sig,
            recovery_chain=recovery_chain,
        )


class MultiStepTrajectory(BaseModel):
    """Aggregated trajectory for a multi-step session.

    Stored alongside individual subtask TrajectoryRecords so planners
    can recall "for complex request X, it decomposed into N subtasks with outcome Y."
    """

    multistep_id: str = Field(default="")
    timestamp: str = Field(default="")
    original_request: str = Field(default="")
    subtask_session_ids: list[str] = Field(default_factory=list)
    subtask_outcomes: list[str] = Field(default_factory=list)
    subtask_descriptions: list[str] = Field(default_factory=list)
    overall_outcome: str = Field(default="")
    total_attempts: int = Field(default=0)


def _build_signature(state: "RuntimeState") -> dict:
    """Extract a structural problem signature from the terminal state."""
    rr = state.semantic_state.reflection_result
    error_type = rr.error_type if rr else ""
    lowered = state.user_request.lower()

    sig: dict = {"error_type": error_type or "none"}

    if "keyerror" in error_type.lower() or "column" in lowered:
        sig["root_cause"] = "missing_dataframe_column"
        sig["domain"] = "pandas"
    elif "importerror" in error_type.lower() or "modulenotfound" in error_type.lower():
        sig["root_cause"] = "missing_import"
        sig["domain"] = "python"
    elif "syntaxerror" in error_type.lower():
        sig["root_cause"] = "syntax_error"
        sig["domain"] = "python"
    elif "filenotfound" in error_type.lower():
        sig["root_cause"] = "missing_file"
        sig["domain"] = "filesystem"
    elif "nameerror" in error_type.lower():
        sig["root_cause"] = "undefined_variable"
        sig["domain"] = "python"
    elif any(k in lowered for k in ("csv", "pandas", "dataframe")):
        sig["root_cause"] = "csv_analysis"
        sig["domain"] = "pandas"
    else:
        sig["root_cause"] = "unknown"
        sig["domain"] = "general"

    return sig
