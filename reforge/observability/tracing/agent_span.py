"""AgentSpan — context manager for agent-level trace spans.

Basic usage (P18.4)::

    with AgentSpan.from_actor(collector, ctx, action="verify", correlation_id=cid):
        result = verifier.verify(hypothesis)

With distributed trace context (P19.1)::

    root_ctx = SpanContext.root()
    child_ctx = root_ctx.child()
    with AgentSpan.from_actor(collector, ctx, "verify",
                               span_context=child_ctx):
        result = verifier.verify(hypothesis)

When *span_context* is supplied, every emitted event also carries
``span_id``, ``parent_span_id``, and ``trace_id`` in its metadata so
``TraceTree`` can later reconstruct the call hierarchy.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from reforge.observability.tracing.models import EventType, TraceEvent

if TYPE_CHECKING:
    from reforge.observability.tracing.collector import TraceCollector
    from reforge.observability.tracing.span_context import SpanContext
    from reforge.runtime.agents.identity import ActorContext


class AgentSpan:
    """Context manager that emits structured trace events for a single agent action.

    Appends to an existing TraceCollector without modifying its internals.
    Optional *span_context* (P19) enables parent-child span linking; without
    it the span behaves exactly as in P18 (backwards-compatible).
    """

    def __init__(
        self,
        collector: TraceCollector,
        actor_id: str,
        actor_role: str,
        session_scope: str,
        action: str,
        correlation_id: str = "",
        span_context: SpanContext | None = None,
    ) -> None:
        self._collector = collector
        self._actor_id = actor_id
        self._actor_role = actor_role
        self._session_scope = session_scope
        self._action = action
        self._correlation_id = correlation_id
        self._span_context = span_context
        self._start_time: float = 0.0

    @classmethod
    def from_actor(
        cls,
        collector: TraceCollector,
        ctx: ActorContext,
        action: str,
        correlation_id: str = "",
        span_context: SpanContext | None = None,
    ) -> AgentSpan:
        """Construct a span from an ActorContext, extracting identity fields."""
        return cls(
            collector=collector,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            session_scope=ctx.session_scope,
            action=action,
            correlation_id=correlation_id,
            span_context=span_context,
        )

    def __enter__(self) -> AgentSpan:
        self._start_time = time.time()
        self._append(EventType.AGENT_ACTION_STARTED)
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: object) -> None:
        duration_ms = (time.time() - self._start_time) * 1000
        if exc_type is None:
            self._append(EventType.AGENT_ACTION_COMPLETED, duration_ms=duration_ms, status="OK")
        else:
            self._append(
                EventType.AGENT_ACTION_FAILED,
                duration_ms=duration_ms,
                status="FAILED",
                extra={"error": str(exc_val), "error_type": exc_type.__name__},
            )
        return None  # never suppress exceptions

    # ------------------------------------------------------------------

    def _append(
        self,
        event_type: EventType,
        duration_ms: float = 0.0,
        status: str = "",
        extra: dict | None = None,
    ) -> None:
        meta: dict = {
            "actor_id": self._actor_id,
            "actor_role": self._actor_role,
            "session_scope": self._session_scope,
            "action": self._action,
            "correlation_id": self._correlation_id,
        }
        if self._span_context is not None:
            meta["span_id"] = self._span_context.span_id
            meta["parent_span_id"] = self._span_context.parent_span_id
            meta["trace_id"] = self._span_context.trace_id
        if extra:
            meta.update(extra)
        self._collector.events.append(
            TraceEvent.create(
                session_id=self._session_scope,
                event_type=event_type,
                duration_ms=duration_ms,
                status=status,
                metadata=meta,
            )
        )
