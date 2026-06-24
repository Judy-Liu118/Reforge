"""RuntimeStage Protocol + RuntimeContext — pipeline architecture for Governor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field

from reforge.runtime.classification.models import FailureClassification
from reforge.runtime.domain.state.models import RuntimeState
from reforge.runtime.orchestration.capability import CapabilityDecision


class PolicyOutcome(BaseModel):
    """Result of PolicyStage — what to do next + how to describe it.

    action is the RuntimeDecisionAction string ("RETRY"/"STOP"/"ACCEPT").
    outcome is the TaskOutcome string. outcome_reason is the canonical
    reason surfaced downstream (observability, RuntimeResolution).
    """

    action: str = Field(default="")
    outcome: str = Field(default="")
    outcome_reason: str = Field(default="")


@dataclass
class RuntimeContext:
    """Intermediate state carried between Governor pipeline stages.

    Each stage writes to its own sub-object — capability (CapabilityStage),
    classification (ClassifyStage), policy (PolicyStage). task_intent stays
    flat because it's a single field with no aggregation value; repair_hint
    stays flat because both ClassifyStage and PolicyStage may touch it.
    """

    state: RuntimeState
    request: str = ""
    task_intent: str = ""
    capability: CapabilityDecision = field(default_factory=CapabilityDecision)
    classification: FailureClassification = field(default_factory=FailureClassification)
    policy: PolicyOutcome = field(default_factory=PolicyOutcome)
    repair_hint: str = ""


class RuntimeStage(Protocol):
    """A single stage in the Governor pipeline. Pure function — no side effects."""

    def execute(self, ctx: RuntimeContext) -> RuntimeContext: ...
