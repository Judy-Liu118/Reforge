"""AgentRole Protocols — three roles that compose a research workflow.

These Protocols are the integration surface for multi-agent runtime:

- `PlannerAgent`     decomposes a question into testable hypotheses
- `VerifierAgent`    executes one hypothesis verification and reports status
- `SynthesizerAgent` aggregates verified hypotheses into a final conclusion

The Protocol form is intentional — any object satisfying the shape qualifies,
so existing classes (`ResearchPlanner`) participate without inheritance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from reforge.runtime.agents.capability import AgentCapability
    from reforge.runtime.research.models import (
        HypothesisRecord,
        ResearchPlan,
    )


class SynthesisResult(BaseModel):
    """Output of a SynthesizerAgent — conclusion text + contradiction list."""

    conclusion: str = Field(default="")
    contradictions: list[str] = Field(default_factory=list)


@runtime_checkable
class PlannerAgent(Protocol):
    """Generates testable hypotheses from a research question."""

    def plan(
        self,
        question: str,
        prior_findings: list[str] | None = None,
        context: str = "",
    ) -> ResearchPlan: ...


@runtime_checkable
class VerifierAgent(Protocol):
    """Executes one hypothesis verification and returns an updated record.

    The returned `HypothesisRecord` carries the same `hypothesis_id` as the
    input but with `status`, `confidence`, and `evidence` populated.
    """

    def verify(self, hypothesis: HypothesisRecord) -> HypothesisRecord: ...


@runtime_checkable
class SynthesizerAgent(Protocol):
    """Aggregates a batch of verified hypotheses into a SynthesisResult."""

    def synthesize(
        self,
        question: str,
        hypotheses: list[HypothesisRecord],
    ) -> SynthesisResult: ...


@runtime_checkable
class CapabilityAware(Protocol):
    """Agent that declares a runtime-level isolation envelope.

    Optional, additive Protocol. Agents not implementing it are treated as
    unrestricted by the SkillRegistry enforcement points.
    """

    @property
    def capability(self) -> AgentCapability: ...
