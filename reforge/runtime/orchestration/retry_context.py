"""Structured RetryContext — gives CodeGen rich failure context instead of a terse string."""

from __future__ import annotations

from dataclasses import dataclass, field

from reforge.runtime.orchestration.evaluation.feedback import format_eval_feedback
from reforge.runtime.domain.state.models import RuntimeState


@dataclass
class RetryContextData:
    """Complete context for a retry attempt — what failed, why, and what to fix."""

    original_request: str = ""
    previous_code: str = ""
    execution_error: str = ""
    reflection_summary: str = ""
    evaluation_feedback: str = ""
    retry_reason: str = ""
    attempt_index: int = 1
    task_intent: str = ""
    constraints: list[str] = field(default_factory=list)

    @classmethod
    def from_state(cls, state: RuntimeState) -> RetryContextData:
        """Build RetryContext from the full runtime state.

        Prefers nested sub-states over flat fields (Phase 2 migration).
        """
        es = state.exec_state
        ss = state.semantic_state
        cs = state.control_state
        er = ss.evaluation_result
        clf = state.classification_result or {}

        execution_error = ""
        exit_code = es.exit_code if es.exit_code is not None else 0
        if exit_code != 0:
            execution_error = state.traceback.strip()
            lines = execution_error.split("\n")
            if len(lines) > 3:
                execution_error = "\n".join(lines[-3:])

        eval_feedback = format_eval_feedback(er) if er else ""

        rr = ss.reflection_result
        return cls(
            original_request=state.user_request,
            previous_code=state.generated_code.strip(),
            execution_error=execution_error,
            reflection_summary=ss.reflection_summary or (rr.error_summary if rr else ""),
            evaluation_feedback=eval_feedback,
            retry_reason=cs.policy_reason or clf.get("failure_mode", "unknown"),
            attempt_index=cs.retry_count + 1,
            task_intent=ss.task_intent or "",
            constraints=_extract_constraints(state),
        )


def _extract_constraints(state: RuntimeState) -> list[str]:
    c: list[str] = []
    if state.task_requirements:
        if state.task_requirements.must_fail_first:
            c.append("must_fail_first")
        if state.task_requirements.expects_uncaught_exception:
            c.append("expects_uncaught_exception")
        if state.task_requirements.expected_final_success:
            c.append("expected_final_success")
    return c


def build_retry_prompt(ctx: RetryContextData) -> str:
    """Build a structured retry prompt from RetryContext.

    Gives CodeGen full context — not just 'error: division by zero'.
    """
    parts: list[str] = []

    parts.append(f"Original task:\n{ctx.original_request}")
    parts.append("")

    if ctx.previous_code:
        # Truncate very long code
        code = ctx.previous_code
        if len(code) > 600:
            code = code[:300] + "\n...\n" + code[-300:]
        parts.append(f"Previous code (attempt {ctx.attempt_index}):\n{code}")
        parts.append("")

    if ctx.execution_error:
        parts.append(f"Execution error:\n{ctx.execution_error}")
        parts.append("")

    if ctx.reflection_summary and ctx.reflection_summary != "Execution succeeded":
        parts.append(f"Root cause:\n{ctx.reflection_summary}")
        parts.append("")

    if ctx.evaluation_feedback:
        parts.append(f"Evaluation feedback:\n{ctx.evaluation_feedback}")
        parts.append("")

    parts.append(f"Retry reason: {ctx.retry_reason}")
    parts.append("")

    if ctx.task_intent:
        parts.append(f"Task intent: {ctx.task_intent}")
        parts.append("")

    if ctx.constraints:
        parts.append(f"Constraints: {', '.join(ctx.constraints)}")
        parts.append("")

    parts.append("Please regenerate improved code that fixes the issue while preserving the original task.")

    return "\n".join(parts)
