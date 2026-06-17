"""TraceTree — assembles flat TraceEvents into a hierarchical span tree.

Events must carry span context fields in their metadata (written by
AgentSpan when a SpanContext is supplied).  Events without ``span_id``
are silently skipped so the tree builder is forward-compatible with
future event sources.

Usage::

    collector = TraceCollector(session_id="sess")
    root_ctx = SpanContext.root()

    with AgentSpan.from_actor(collector, planner_ctx, "plan",
                               span_context=root_ctx):
        with AgentSpan.from_actor(collector, verifier_ctx, "verify",
                                   span_context=root_ctx.child()):
            ...

    tree = TraceTree(collector.events)
    roots = tree.build()          # list[TraceNode]
    print(render_trace_tree(roots))

Output::

    [planner:plan] [OK] 5.2ms  span=a1b2c3d4
      [verifier:verify] [OK] 12.1ms  span=e5f6g7h8
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from reforge.observability.tracing.models import EventType

if TYPE_CHECKING:
    from reforge.observability.tracing.models import TraceEvent


@dataclass
class TraceNode:
    """One span in the trace tree, with its children attached."""

    span_id: str
    parent_span_id: str
    trace_id: str
    actor_id: str
    actor_role: str
    action: str
    timestamp: str
    status: str = ""
    duration_ms: float = 0.0
    children: list[TraceNode] = field(default_factory=list)


class TraceTree:
    """Assembles a list of TraceEvents with span context into a call tree.

    Algorithm:
      1. Create a TraceNode for each AGENT_ACTION_STARTED event that has
         ``span_id`` in its metadata.
      2. Apply status/duration from matching COMPLETED or FAILED events.
      3. Link parent → child based on ``parent_span_id``.
      4. Return root nodes (spans with no known parent).
    """

    def __init__(self, events: list[TraceEvent]) -> None:
        self._events = events

    def build(self) -> list[TraceNode]:
        """Return root TraceNodes with children recursively attached."""
        nodes: dict[str, TraceNode] = {}

        # Pass 1: create nodes from STARTED events
        for event in self._events:
            if event.event_type != EventType.AGENT_ACTION_STARTED:
                continue
            meta = event.metadata
            span_id = meta.get("span_id")
            if not span_id:
                continue
            nodes[span_id] = TraceNode(
                span_id=span_id,
                parent_span_id=meta.get("parent_span_id", ""),
                trace_id=meta.get("trace_id", ""),
                actor_id=meta.get("actor_id", ""),
                actor_role=meta.get("actor_role", ""),
                action=meta.get("action", ""),
                timestamp=event.timestamp,
            )

        # Pass 2: apply terminal-event fields (status, duration)
        for event in self._events:
            if event.event_type not in (
                EventType.AGENT_ACTION_COMPLETED,
                EventType.AGENT_ACTION_FAILED,
            ):
                continue
            span_id = event.metadata.get("span_id")
            if span_id and span_id in nodes:
                nodes[span_id].status = event.status
                nodes[span_id].duration_ms = event.duration_ms

        # Pass 3: link children; collect roots
        roots: list[TraceNode] = []
        for node in nodes.values():
            parent = nodes.get(node.parent_span_id)
            if parent is not None:
                parent.children.append(node)
            else:
                roots.append(node)

        return roots

    def all_nodes(self) -> list[TraceNode]:
        """Return every node as a flat list (depth-first)."""

        def _flatten(nodes: list[TraceNode]) -> list[TraceNode]:
            result: list[TraceNode] = []
            for n in nodes:
                result.append(n)
                result.extend(_flatten(n.children))
            return result

        return _flatten(self.build())

    def trace_ids(self) -> set[str]:
        """Return the set of distinct trace_ids present in the tree."""
        return {n.trace_id for n in self.all_nodes() if n.trace_id}


def render_trace_tree(roots: list[TraceNode], _indent: int = 0) -> str:
    """Render a list of root TraceNodes as an indented text tree."""
    lines: list[str] = []
    prefix = "  " * _indent
    for node in roots:
        status_part = f" [{node.status}]" if node.status else ""
        dur_part = f" {node.duration_ms:.1f}ms" if node.duration_ms > 0 else ""
        lines.append(
            f"{prefix}[{node.actor_role}:{node.action}]{status_part}{dur_part}"
            f"  span={node.span_id[:8]}"
        )
        if node.children:
            lines.append(render_trace_tree(node.children, _indent + 1))
    return "\n".join(filter(None, lines))
