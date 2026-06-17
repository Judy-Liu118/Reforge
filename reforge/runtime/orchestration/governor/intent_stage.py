"""IntentStage — classify user task intent via LLM few-shot."""

from reforge.runtime.orchestration.governor.stages import RuntimeContext
from reforge.runtime.policy.task_intent import classify_intent


class IntentStage:
    """Stage 1: Classify task intent."""

    def execute(self, ctx: RuntimeContext) -> RuntimeContext:
        intent = classify_intent(ctx.request)
        ctx.task_intent = intent.value
        return ctx
