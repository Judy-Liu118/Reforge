"""ExecutionGovernor — pipeline composition entry point."""

from __future__ import annotations

from pydantic import BaseModel, Field

from reforge.runtime.orchestration.governor.capability_stage import CapabilityStage
from reforge.runtime.orchestration.governor.classify_stage import ClassifyStage
from reforge.runtime.orchestration.governor.intent_stage import IntentStage
from reforge.runtime.orchestration.governor.policy_stage import PolicyStage
from reforge.runtime.orchestration.governor.stages import RuntimeContext
from reforge.runtime.orchestration.outcome_resolver import TaskOutcome
from reforge.runtime.domain.state.models import RuntimeState


class RuntimeResolution(BaseModel):
    action: str = Field(default="")
    outcome: str = Field(default="")
    reason: str = Field(default="")
    risk_level: str = Field(default="low")
    task_intent: str = Field(default="")
    failure_mode: str = Field(default="")
    intentional: bool = Field(default=False)
    retryable: bool = Field(default=False)
    repair_hint: str | None = Field(default=None)


class ExecutionGovernor:
    """Sole runtime authority. Pipeline: intent → capability → classify → policy."""

    def __init__(self, max_retries: int = 2) -> None:
        self._stages = [
            IntentStage(),
            CapabilityStage(),
            ClassifyStage(),
            PolicyStage(max_retries=max_retries),
        ]

    def resolve(self, state: RuntimeState) -> RuntimeResolution:
        ctx = RuntimeContext(state=state, request=state.user_request)
        for stage in self._stages:
            ctx = stage.execute(ctx)
            if not ctx.capability.allow:
                return RuntimeResolution(
                    action="DENY", outcome=TaskOutcome.DENIED.value,
                    reason=ctx.capability.deny_category,
                    risk_level=ctx.capability.risk_level,
                    task_intent=ctx.task_intent,
                )
        return RuntimeResolution(
            action=ctx.policy.action, outcome=ctx.policy.outcome, reason=ctx.policy.outcome_reason,
            task_intent=ctx.task_intent, failure_mode=ctx.classification.failure_mode,
            intentional=ctx.classification.intentional, retryable=ctx.classification.retryable,
            repair_hint=ctx.repair_hint or None,
        )
