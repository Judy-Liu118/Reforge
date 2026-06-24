"""FailureClassification — separates intent/retryable classification from reflection."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FailureClassification(BaseModel):
    """Structured failure classification produced by the classifier.

    Separates "what went wrong" (reflection) from "what to do about it" (policy).
    """

    is_expected_failure: bool = Field(default=False)
    retryable: bool = Field(default=False)
    failure_mode: str = Field(default="")
    severity: str = Field(default="low")
    confidence: float = Field(default=1.0)
