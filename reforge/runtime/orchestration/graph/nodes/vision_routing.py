"""Vision-routing node — decides whether code_generation needs multimodal LLM.

Computes the VisionRouting decision once, after capability_check and before
code_generation. Keeps filesystem IO (scanning workspace for target images)
out of code_generation_node, which should stay a pure state→state function.

Retry loops do NOT re-enter this node — the graph re-runs code_generation
directly on retry, and the routing decision cached on state.vision_routing
is reused. That's correct: user_request and workspace are stable within a
session, so the decision is idempotent.
"""

from __future__ import annotations

from pathlib import Path

from reforge.runtime.orchestration.graph.vision_routing import resolve_vision_routing
from reforge.runtime.domain.state.models import RuntimeState


def vision_routing_node(state: RuntimeState, *, workspace: Path) -> dict:
    decision = resolve_vision_routing(state.user_request, workspace=workspace)
    return {"vision_routing": decision}
