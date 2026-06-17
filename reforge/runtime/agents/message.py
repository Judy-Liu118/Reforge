"""RuntimeMessage — typed message contract for the multi-agent bus.

All inter-agent communication must be expressed as RuntimeMessage so the bus
can route, dispatch, and log without interpreting payload semantics.

The model is frozen so a message is never mutated after creation; each hop in
the delivery chain works from the same immutable envelope.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class RuntimeMessage(BaseModel):
    """Typed envelope for all inter-agent communication.

    Attributes:
        message_type:   Semantic label for the message (e.g. "verify_request",
                        "plan_result"). The bus routes on this; actors interpret it.
        sender:         ActorContext.actor_id of the emitting actor.
        recipient:      ActorContext.actor_id of the target actor, or a role
                        string for broadcast dispatch (e.g. "verifier").
        payload:        Message-type-specific data. The bus never inspects this.
        correlation_id: UUID string that links a request to its response(s) and
                        to per-agent trace spans.
        timestamp:      UTC creation time; auto-filled by default.
    """

    message_type: str
    sender: str
    recipient: str
    payload: dict = Field(default_factory=dict)
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    model_config = {"frozen": True}

    @classmethod
    def create(
        cls,
        message_type: str,
        sender: str,
        recipient: str,
        payload: dict | None = None,
        correlation_id: str | None = None,
    ) -> RuntimeMessage:
        """Convenience constructor; auto-fills correlation_id and timestamp."""
        return cls(
            message_type=message_type,
            sender=sender,
            recipient=recipient,
            payload=payload or {},
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
