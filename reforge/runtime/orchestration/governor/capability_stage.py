"""CapabilityStage — pre-execution security gate."""

from reforge.runtime.orchestration.capability import SemanticSafetyGuard
from reforge.runtime.orchestration.governor.stages import RuntimeContext


class CapabilityStage:
    """Stage 2: Check request against semantic safety heuristics."""

    def __init__(self) -> None:
        self._engine = SemanticSafetyGuard()

    def execute(self, ctx: RuntimeContext) -> RuntimeContext:
        ctx.capability = self._engine.check(ctx.request, task_intent=ctx.task_intent)
        return ctx
