"""MessageBus — synchronous routing layer for inter-agent communication.

The bus routes RuntimeMessage instances to registered handlers by actor_id
or role name.  It never inspects payload content and carries no cognition:
retry policy, evaluation, synthesis, and semantic arbitration all belong to
callers, not the bus.

Routing rules:
  send(msg):
    1. recipient matches an actor_id  → exact dispatch to that handler
    2. recipient matches a role name  → dispatch to the first-registered
       handler for that role (deterministic single dispatch)
    3. no match                       → BusRoutingError

  send_all(msg):
    1. recipient matches a role name  → broadcast to ALL handlers for that
       role; returns responses in registration order
    2. recipient matches an actor_id  → dispatch to that one actor; returns
       single-element list (consistent return type)
    3. no match                       → BusRoutingError

Session scope: one bus instance per research session.  Not a global
singleton; create it alongside the session and discard it afterwards.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from reforge.runtime.agents.identity import ActorContext
from reforge.runtime.agents.message import RuntimeMessage

MessageHandler = Callable[[RuntimeMessage], RuntimeMessage]


class BusRoutingError(KeyError):
    """Raised when no registered handler matches the message recipient."""


class MessageBus:
    """Routes RuntimeMessage instances to registered handlers.

    Handlers are plain callables: ``(RuntimeMessage) -> RuntimeMessage``.
    The bus never calls into the handler before ``send``/``send_all`` —
    registration is purely additive.
    """

    def __init__(self) -> None:
        # actor_id → (ActorContext, handler)
        self._handlers: dict[str, tuple[ActorContext, MessageHandler]] = {}
        # role → [actor_id, ...] in registration order
        self._role_index: dict[str, list[str]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, ctx: ActorContext, handler: MessageHandler) -> None:
        """Register a handler for *ctx*.

        Re-registering the same actor_id replaces the previous handler but
        preserves the role-index position so routing order is stable.
        """
        if ctx.actor_id not in self._handlers:
            self._role_index[ctx.actor_role].append(ctx.actor_id)
        self._handlers[ctx.actor_id] = (ctx, handler)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def send(self, message: RuntimeMessage) -> RuntimeMessage:
        """Dispatch *message* to a single handler; return the response.

        Raises BusRoutingError when recipient matches nothing.
        """
        handler = self._resolve_one(message.recipient)
        return handler(message)

    def send_all(self, message: RuntimeMessage) -> list[RuntimeMessage]:
        """Broadcast *message* to all handlers for a role; return all responses.

        When recipient is an actor_id, behaves like send() but returns a
        single-element list so call sites always get ``list[RuntimeMessage]``.

        Raises BusRoutingError when recipient matches nothing.
        """
        actor_ids = self._resolve_all(message.recipient)
        return [self._handlers[aid][1](message) for aid in actor_ids]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def registered_actors(self) -> list[ActorContext]:
        """All registered ActorContext instances in registration order."""
        return [ctx for ctx, _ in self._handlers.values()]

    def has_handler(self, actor_id_or_role: str) -> bool:
        """Return True when at least one handler matches the token."""
        return (
            actor_id_or_role in self._handlers
            or bool(self._role_index.get(actor_id_or_role))
        )

    def __len__(self) -> int:
        return len(self._handlers)

    def __repr__(self) -> str:
        roles = ", ".join(
            f"{role}×{len(ids)}" for role, ids in sorted(self._role_index.items())
        )
        return f"MessageBus({roles})"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_one(self, recipient: str) -> MessageHandler:
        """Return the handler for *recipient*; exact actor_id then role."""
        if recipient in self._handlers:
            return self._handlers[recipient][1]
        ids = self._role_index.get(recipient, [])
        if ids:
            return self._handlers[ids[0]][1]
        self._raise_routing_error(recipient)

    def _resolve_all(self, recipient: str) -> list[str]:
        """Return actor_id list for broadcast; role first, then actor_id."""
        ids = self._role_index.get(recipient, [])
        if ids:
            return list(ids)
        if recipient in self._handlers:
            return [recipient]
        self._raise_routing_error(recipient)

    def _raise_routing_error(self, recipient: str) -> None:
        registered = sorted(
            list(self._handlers) + list(self._role_index)
        )
        hint = f"Registered: {registered}" if registered else "Bus is empty."
        raise BusRoutingError(
            f"No handler for recipient={recipient!r}. {hint}"
        )
