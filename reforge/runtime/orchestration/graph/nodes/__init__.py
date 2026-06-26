"""Graph node implementations for the runtime workflow.

Each node owns a single responsibility:
- planner: produce an initial plan + extract process constraints
- capability: enforce capability policy gates
- codegen: emit Python code, augmented with retry context (text or vision
  path chosen per attempt by bool(state.image_inputs))
- execution: run code in sandbox and collect output
- reflection: LLM-based traceback analysis when execution failed
- evaluation: heuristic post-execution checks
- retry_decision: governor pipeline → RETRY/STOP/ACCEPT
- final_response: assemble the final answer + resolve task outcome
"""

from __future__ import annotations

from reforge.runtime.orchestration.graph.nodes.capability import (
    capability_node,
    route_after_capability,
)
from reforge.runtime.orchestration.graph.nodes.codegen import code_generation_node
from reforge.runtime.orchestration.graph.nodes.evaluation import evaluation_node
from reforge.runtime.orchestration.graph.nodes.execution import execution_node
from reforge.runtime.orchestration.graph.nodes.final_response import final_response_node
from reforge.runtime.orchestration.graph.nodes.planner import planner_node
from reforge.runtime.orchestration.graph.nodes.reflection import reflection_node
from reforge.runtime.orchestration.graph.nodes.retry_decision import (
    retry_decision_node,
    should_retry,
)

__all__ = [
    "capability_node",
    "code_generation_node",
    "evaluation_node",
    "execution_node",
    "final_response_node",
    "planner_node",
    "reflection_node",
    "retry_decision_node",
    "route_after_capability",
    "should_retry",
]
