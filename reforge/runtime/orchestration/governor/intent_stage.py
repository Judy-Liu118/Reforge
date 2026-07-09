"""IntentStage — classify user task intent via LLM few-shot.

Intent is a property of the *request*, not of any execution attempt, so it
is classified at most once per session: the first governor resolve calls
the LLM, `retry_decision_node` persists the result on
`semantic_state.task_intent`, and every later resolve in the same session
reuses that value. Without the cache, an N-attempt session would re-run
the same classification N times — paying N LLM calls and risking a
mid-run intent flip changing policy between attempts.
"""

from reforge.runtime.orchestration.governor.stages import RuntimeContext
from reforge.runtime.policy.task_intent import classify_intent


class IntentStage:
    """Stage 1: Classify task intent (cached on state across attempts)."""

    def execute(self, ctx: RuntimeContext) -> RuntimeContext:
        cached = ctx.state.semantic_state.task_intent
        if cached:
            ctx.task_intent = cached
            return ctx
        intent = classify_intent(ctx.request)
        ctx.task_intent = intent.value
        return ctx
