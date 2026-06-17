"""LangGraph workflow builder for the self-healing execution loop.

Node implementations live in `reforge.runtime.orchestration.graph.nodes`. This file only
wires them into the graph; it owns no business logic.

A MemorySubstrate may be injected at build time — the planner and reflection
nodes will both query it for past experience. When omitted, each node falls
back to its default (CompositeMemorySubstrate).

An ExecutionEventLog + session_id may also be injected.  When provided, the
execution / evaluation / reflection / retry_decision nodes emit ExecutionEvents
around each lifecycle transition.  When omitted, behavior is unchanged.
"""

from __future__ import annotations

from reforge.memory.substrate import MemorySubstrate
from reforge.runtime.events.emitters import (
    wrap_evaluation_node,
    wrap_execution_node,
    wrap_final_response_node,
    wrap_reflection_node,
    wrap_retry_decision_node,
)
from reforge.runtime.events.log import ExecutionEventLog
from langgraph.graph import END, StateGraph
from reforge.runtime.orchestration.graph.nodes import (
    capability_node,
    code_generation_node,
    evaluation_node,
    execution_node,
    final_response_node,
    planner_node,
    reflection_node,
    retry_decision_node,
    route_after_capability,
    should_retry,
)
from reforge.runtime.domain.state.models import RuntimeState


def build_graph(
    memory_substrate: MemorySubstrate | None = None,
    event_log: ExecutionEventLog | None = None,
    session_id: str = "",
) -> StateGraph:
    graph = StateGraph(RuntimeState)

    def _planner(state: RuntimeState) -> dict:
        return planner_node(state, substrate=memory_substrate)

    def _reflection_base(state: RuntimeState) -> dict:
        return reflection_node(state, substrate=memory_substrate)

    _execution = wrap_execution_node(execution_node, event_log, session_id)
    _evaluation = wrap_evaluation_node(evaluation_node, event_log, session_id)
    _reflection = wrap_reflection_node(_reflection_base, event_log, session_id)
    _retry_decision = wrap_retry_decision_node(retry_decision_node, event_log, session_id)
    _final_response = wrap_final_response_node(final_response_node, event_log, session_id)

    graph.add_node("planner", _planner)
    graph.add_node("capability_check", capability_node)
    graph.add_node("code_generation", code_generation_node)
    graph.add_node("execution", _execution)
    graph.add_node("reflection", _reflection)
    graph.add_node("evaluation", _evaluation)
    graph.add_node("retry_decision", _retry_decision)
    graph.add_node("final_response", _final_response)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "capability_check")
    graph.add_conditional_edges(
        "capability_check",
        route_after_capability,
        {"code_generation": "code_generation", "final_response": "final_response"},
    )
    graph.add_edge("code_generation", "execution")
    graph.add_edge("execution", "reflection")
    graph.add_edge("reflection", "evaluation")
    graph.add_edge("evaluation", "retry_decision")
    graph.add_conditional_edges(
        "retry_decision",
        should_retry,
        {"code_generation": "code_generation", "final_response": "final_response"},
    )
    graph.add_edge("final_response", END)

    return graph.compile()
