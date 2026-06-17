"""SpanContext — immutable distributed trace context.

Carries three identifiers:
  trace_id     — shared across every span in one logical operation (e.g. one
                 research session).  Generated once at the root; propagated
                 unchanged through all child spans.
  span_id      — unique identifier for this specific span.
  parent_span_id — span_id of the parent span; empty string for root spans.

Usage::

    # Root span — start of a research session
    root_ctx = SpanContext.root()

    # Child span — one verifier call inside that session
    verifier_ctx = root_ctx.child()

    with AgentSpan.from_actor(collector, actor_ctx, "verify",
                               span_context=verifier_ctx):
        result = verifier.verify(hypothesis)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


def _short_id(length: int = 16) -> str:
    return uuid.uuid4().hex[:length]


@dataclass(frozen=True)
class SpanContext:
    """Immutable carrier for distributed trace identity."""

    trace_id: str
    span_id: str
    parent_span_id: str = ""

    @classmethod
    def root(cls, trace_id: str | None = None) -> SpanContext:
        """Create a root span context (no parent).

        *trace_id* can be supplied explicitly to attach this span to an
        existing trace; otherwise a new trace is started.
        """
        return cls(
            trace_id=trace_id or _short_id(16),
            span_id=_short_id(12),
            parent_span_id="",
        )

    def child(self) -> SpanContext:
        """Return a new child SpanContext that inherits *trace_id* from this span."""
        return SpanContext(
            trace_id=self.trace_id,
            span_id=_short_id(12),
            parent_span_id=self.span_id,
        )

    @property
    def is_root(self) -> bool:
        return self.parent_span_id == ""
