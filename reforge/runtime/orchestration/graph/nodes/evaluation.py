"""Evaluation node — heuristic post-execution checks.

Annotates the most recent AttemptRecord with eval_score / eval_failure_type
so trajectories and history can show per-attempt eval trends.
"""

from __future__ import annotations

from reforge.runtime.orchestration.evaluation.heuristics import HeuristicEvaluator
from reforge.runtime.domain.state.models import EvaluationResult, RuntimeState


def evaluation_node(state: RuntimeState) -> dict:
    evaluator = HeuristicEvaluator()
    result = evaluator.evaluate(state)

    updated_attempts = list(state.attempts)
    if updated_attempts:
        last = updated_attempts[-1]
        updated_attempts[-1] = last.model_copy(
            update={
                "eval_score": result.score,
                "eval_failure_type": result.failure_type,
            }
        )

    er = EvaluationResult(
        passed=result.passed,
        score=result.score,
        checks=result.checks,
        summary=result.summary,
        failure_type=result.failure_type,
    )
    return {
        "evaluation_result": er.model_dump(),  # legacy key consumed by emitter only
        "semantic_state": state.semantic_state.model_copy(update={"evaluation_result": er}),
        "attempts": [a.model_dump() for a in updated_attempts],
    }
