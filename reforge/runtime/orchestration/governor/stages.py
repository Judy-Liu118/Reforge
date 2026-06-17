"""RuntimeStage Protocol + RuntimeContext — pipeline architecture for Governor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from reforge.runtime.domain.state.models import RuntimeState


@dataclass
class RuntimeContext:
    """Intermediate state carried between Governor pipeline stages."""

    state: RuntimeState
    request: str = ""
    task_intent: str = ""
    capability_allow: bool = True
    capability_reason: str = ""
    capability_risk: str = "low"
    failure_mode: str = ""
    intentional: bool = False
    retryable: bool = False
    policy_action: str = ""
    policy_reason: str = ""
    outcome: str = ""
    outcome_reason: str = ""


class RuntimeStage(Protocol):
    """A single stage in the Governor pipeline. Pure function — no side effects."""

    def execute(self, ctx: RuntimeContext) -> RuntimeContext: ...
