"""CapabilityStage — pre-execution security gate."""

from reforge.runtime.orchestration.capability import SemanticSafetyGuard
from reforge.runtime.orchestration.governor.stages import RuntimeContext


class CapabilityStage:
    """Stage 2: Check request against semantic safety heuristics."""

    def __init__(self) -> None:
        self._engine = SemanticSafetyGuard()

    def execute(self, ctx: RuntimeContext) -> RuntimeContext:
        cap = self._engine.check(ctx.request)
        ctx.capability_allow = cap.allow
        ctx.capability_reason = cap.reason
        ctx.capability_risk = cap.risk_level
        return ctx
