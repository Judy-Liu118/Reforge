"""Research runtime models — hypothesis-driven iterative investigation."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class HypothesisRecord(BaseModel):
    """One testable claim with its verification approach and collected evidence."""

    hypothesis_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    hypothesis: str = Field(default="")
    rationale: str = Field(default="")
    verification_request: str = Field(default="")
    status: Literal["pending", "confirmed", "rejected", "inconclusive"] = Field(
        default="pending"
    )
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    round_number: int = Field(default=0)


class ResearchPlan(BaseModel):
    """Output of ResearchPlanner: set of hypotheses for one investigation round."""

    question: str = Field(default="")
    hypotheses: list[HypothesisRecord] = Field(default_factory=list)
    reasoning: str = Field(default="")


class ResearchRound(BaseModel):
    """Summary of one investigation round."""

    round_number: int = Field(default=0)
    hypotheses_tested: list[str] = Field(default_factory=list)
    new_findings: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class ResearchResult(BaseModel):
    """Complete output of a multi-round ResearchSession."""

    research_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: str = Field(default="")
    question: str = Field(default="")
    rounds: list[ResearchRound] = Field(default_factory=list)
    final_hypotheses: list[HypothesisRecord] = Field(default_factory=list)
    conclusion: str = Field(default="")
    contradictions_detected: list[str] = Field(default_factory=list)
    total_rounds: int = Field(default=0)
