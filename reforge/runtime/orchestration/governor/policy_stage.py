"""PolicyStage — retry policy decision + outcome resolution."""

from reforge.runtime.domain.state.models import TIMEOUT_EXIT_CODE
from reforge.runtime.orchestration.governor.stages import RuntimeContext
from reforge.runtime.orchestration.outcome_resolver import resolve_outcome
from reforge.runtime.policy.retry_policy import RetryPolicy


class PolicyStage:
    """Stage 4: Decide RETRY/STOP/ACCEPT + resolve outcome."""

    def __init__(self, max_retries: int = 2) -> None:
        self._max_retries = max_retries
        self._policy = RetryPolicy()

    def execute(self, ctx: RuntimeContext) -> RuntimeContext:
        execution = ctx.state.execution_output
        evaluation = ctx.state.semantic_state.evaluation_result

        decision = self._policy.decide(
            classification={
                "is_expected_failure": ctx.classification.is_expected_failure,
                "retryable": ctx.classification.retryable,
                "failure_mode": ctx.classification.failure_mode,
            },
            execution=execution,
            evaluation=evaluation,
            retry_count=ctx.state.control_state.retry_count,
            max_retries=self._max_retries,
        )

        outcome, reason = resolve_outcome(
            task_intent=ctx.task_intent,
            execution_exit_code=execution.exit_code if execution else TIMEOUT_EXIT_CODE,
            retry_count=ctx.state.control_state.retry_count,
            eval_passed=evaluation.passed if evaluation else True,
            policy_action=decision.action.value,
        )

        ctx.policy.action = decision.action.value
        ctx.policy.outcome = outcome.value
        ctx.policy.outcome_reason = reason
        return ctx
