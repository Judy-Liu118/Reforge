"""PolicyStage — retry policy decision + outcome resolution."""

from reforge.runtime.orchestration.governor.stages import RuntimeContext
from reforge.runtime.orchestration.outcome_resolver import resolve_outcome
from reforge.runtime.policy.retry_policy import RetryPolicy


class PolicyStage:
    """Stage 4: Decide RETRY/STOP/ACCEPT + resolve outcome."""

    def __init__(self, max_retries: int = 2) -> None:
        self._max_retries = max_retries
        self._policy = RetryPolicy()

    def execute(self, ctx: RuntimeContext) -> RuntimeContext:
        eo = ctx.state.execution_output
        er = ctx.state.semantic_state.evaluation_result

        decision = self._policy.decide(
            classification={
                "intentional": ctx.intentional,
                "retryable": ctx.retryable,
                "failure_mode": ctx.failure_mode,
            },
            execution=eo,
            evaluation=er,
            retry_count=ctx.state.control_state.retry_count,
            max_retries=self._max_retries,
        )

        outcome, reason = resolve_outcome(
            task_intent=ctx.task_intent,
            execution_exit_code=eo.exit_code if eo else -1,
            retry_count=ctx.state.control_state.retry_count,
            eval_passed=er.passed if er else True,
            policy_action=decision.action.value,
        )

        ctx.policy_action = decision.action.value
        ctx.policy_reason = decision.reason
        ctx.outcome = outcome.value
        ctx.outcome_reason = reason
        return ctx
