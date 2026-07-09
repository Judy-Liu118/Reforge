"""Planner node — produce an initial plan + extract process constraints.

The plan lands on `semantic_state.plan` and is injected into the codegen
prompt (see codegen.py) — this is also where memory influences the *first*
attempt: PlannerMemoryContext prepends similar past sessions to the planner
prompt, and the resulting plan carries that context forward.
"""

from __future__ import annotations

from reforge.memory.substrate import MemorySubstrate
from reforge.models.adapters.llm_client import LLMClient
from reforge.models.prompts.templates import PLANNER_SYSTEM
from reforge.runtime.orchestration.reflection.planner_context import PlannerMemoryContext
from reforge.runtime.infrastructure.requirements import extract_requirements
from reforge.runtime.domain.state.models import RuntimeState


def planner_node(
    state: RuntimeState,
    *,
    substrate: MemorySubstrate | None = None,
) -> dict:
    llm = LLMClient()

    ctx = PlannerMemoryContext(substrate=substrate)
    memory_context = ctx.build(state.user_request)
    user_msg = (
        f"{memory_context}\n\n---\nTask: {state.user_request}"
        if memory_context
        else state.user_request
    )

    plan = llm.chat(PLANNER_SYSTEM, user_msg)
    reqs = extract_requirements(state.user_request)
    return {
        "semantic_state": state.semantic_state.model_copy(update={"plan": plan}),
        "task_requirements": reqs,
    }
