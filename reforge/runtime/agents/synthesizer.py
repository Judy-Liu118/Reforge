"""DefaultSynthesizer — combines EvidenceAggregator contradictions with a
deterministic conclusion renderer over confirmed/rejected/inconclusive groups.

The conclusion format is the same one ResearchSession used inline pre-P17;
extracted so alternative SynthesizerAgent implementations can replace it.
"""

from __future__ import annotations

from reforge.runtime.agents.capability import AgentCapability, unrestricted
from reforge.runtime.agents.role import SynthesisResult
from reforge.runtime.research.aggregator import EvidenceAggregator
from reforge.runtime.research.models import HypothesisRecord


def render_conclusion(question: str, hypotheses: list[HypothesisRecord]) -> str:
    """Format confirmed / rejected / inconclusive groups under the question."""
    confirmed = [h for h in hypotheses if h.status == "confirmed"]
    rejected = [h for h in hypotheses if h.status == "rejected"]
    inconclusive = [h for h in hypotheses if h.status == "inconclusive"]

    parts = [f"Research question: {question}"]
    if confirmed:
        parts.append(
            f"Confirmed ({len(confirmed)}): "
            + "; ".join(h.hypothesis for h in confirmed)
        )
    if rejected:
        parts.append(
            f"Rejected ({len(rejected)}): "
            + "; ".join(h.hypothesis for h in rejected)
        )
    if inconclusive:
        parts.append(
            f"Inconclusive ({len(inconclusive)}): "
            + "; ".join(h.hypothesis for h in inconclusive)
        )
    return "\n".join(parts)


class DefaultSynthesizer:
    """SynthesizerAgent backed by EvidenceAggregator contradiction detection.

    Carries an `AgentCapability` so downstream isolation enforcement can
    treat the synthesizer uniformly with other agents (typically read-only
    over memory).
    """

    def __init__(
        self,
        aggregator: EvidenceAggregator | None = None,
        capability: AgentCapability | None = None,
    ) -> None:
        self._aggregator = aggregator or EvidenceAggregator()
        self._capability = capability or unrestricted("synthesizer")

    @property
    def capability(self) -> AgentCapability:
        return self._capability

    def synthesize(
        self,
        question: str,
        hypotheses: list[HypothesisRecord],
    ) -> SynthesisResult:
        contradictions = self._aggregator.detect_contradictions(hypotheses)
        conclusion = render_conclusion(question, hypotheses)
        return SynthesisResult(
            conclusion=conclusion,
            contradictions=contradictions,
        )
