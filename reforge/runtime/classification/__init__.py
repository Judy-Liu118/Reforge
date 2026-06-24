"""Lazy re-exports to avoid pulling in classifier (which depends on
runtime.domain.state.models) when state.models itself imports
FailureClassification — eager imports here would create a cycle.
"""
from __future__ import annotations

from reforge.runtime.classification.models import FailureClassification

__all__ = ["FailureClassification", "FailureClassifier"]


def __getattr__(name: str):
    if name == "FailureClassifier":
        from reforge.runtime.classification.classifier import FailureClassifier
        return FailureClassifier
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
