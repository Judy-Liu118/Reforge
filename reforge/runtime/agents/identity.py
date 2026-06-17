"""ActorContext — lightweight runtime identity for multi-agent actors.

Every actor in the multi-agent runtime carries an identity so that registry
lookup, message routing, trace correlation, and scoped memory can reference
the same actor unambiguously within a session.

Intentionally minimal: a frozen dataclass, no actor framework, no lifecycle
management. P18.1 (registry) and P18.2 (bus) build on top of this identity.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class ActorContext:
    """Immutable identity token carried by every runtime actor.

    Attributes:
        actor_id:      Unique identifier for this actor instance (UUID string).
        actor_role:    Semantic role — "planner", "verifier", "synthesizer",
                       or any custom string. Open-ended to stay extensible.
        session_scope: Binds this actor to a specific research session so
                       routing, tracing, and memory stay scoped correctly.
    """

    actor_id: str
    actor_role: str
    session_scope: str

    @classmethod
    def create(cls, actor_role: str, session_scope: str) -> ActorContext:
        """Mint a new ActorContext with a fresh unique actor_id."""
        return cls(
            actor_id=str(uuid.uuid4()),
            actor_role=actor_role,
            session_scope=session_scope,
        )
