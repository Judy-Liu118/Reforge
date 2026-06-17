"""Semantic safety gate — denies dangerous requests before code generation."""

from __future__ import annotations

from typing import Literal

from reforge.runtime.orchestration.capability import SemanticSafetyGuard
from reforge.runtime.domain.state.models import RuntimeState


def capability_node(state: RuntimeState) -> dict:
    engine = SemanticSafetyGuard()
    cap = engine.check(state.user_request)
    return {
        "capability_decision": cap.model_dump() if not cap.allow else None,
    }


def route_after_capability(
    state: RuntimeState,
) -> Literal["code_generation", "final_response"]:
    if state.capability_decision:
        return "final_response"
    return "code_generation"
