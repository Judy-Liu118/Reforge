"""AgentRegistry — runtime lookup table for multi-agent role implementations.

Maps (role, variant) pairs to agent implementations so call sites swap
backends without changing their code.  A registry holds no cognition: it
stores, looks up, and creates identity tokens.  Orchestration belongs
elsewhere.

Typical usage:

    registry = AgentRegistry()
    registry.register("verifier", RunnerVerifier(...))
    registry.register("verifier", MockVerifier(), variant="mock")

    ctx, agent = registry.create_actor("verifier", session_scope="sess-1")
    # ctx  → ActorContext(actor_id=<uuid>, actor_role="verifier", ...)
    # agent → the RunnerVerifier instance
"""

from __future__ import annotations

from typing import Any

from reforge.runtime.agents.identity import ActorContext

_DEFAULT_VARIANT = "default"


class RegistryKeyError(KeyError):
    """Raised when a (role, variant) pair has no registered implementation."""


class AgentRegistry:
    """Lookup table: (actor_role, variant) → agent implementation.

    Variants let callers register multiple implementations for the same role
    and switch between them at runtime without touching call sites.
    Built-in variant names: "default", "experimental", "mock".
    Custom strings are also accepted.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Any] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        role: str,
        agent: Any,
        variant: str = _DEFAULT_VARIANT,
    ) -> None:
        """Register an agent implementation for a role/variant pair.

        Calling register() a second time with the same (role, variant)
        silently replaces the previous entry — intentional for runtime swap.
        """
        self._store[(role, variant)] = agent

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, role: str, variant: str = _DEFAULT_VARIANT) -> Any:
        """Return the registered agent; raise RegistryKeyError if absent."""
        key = (role, variant)
        try:
            return self._store[key]
        except KeyError:
            registered = [
                f"{r!r}:{v!r}" for r, v in sorted(self._store)
            ]
            hint = f"Registered: {registered}" if registered else "Registry is empty."
            raise RegistryKeyError(
                f"No agent for role={role!r} variant={variant!r}. {hint}"
            ) from None

    def get_or_none(
        self, role: str, variant: str = _DEFAULT_VARIANT
    ) -> Any | None:
        """Return the registered agent, or None if not found."""
        return self._store.get((role, variant))

    def has(self, role: str, variant: str = _DEFAULT_VARIANT) -> bool:
        """Return True when a registration exists for (role, variant)."""
        return (role, variant) in self._store

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def registered_roles(self) -> list[str]:
        """Deduplicated, sorted list of all registered role names."""
        return sorted({role for role, _ in self._store})

    def registered_variants(self, role: str) -> list[str]:
        """Sorted list of variant names registered for a specific role."""
        return sorted(v for r, v in self._store if r == role)

    # ------------------------------------------------------------------
    # Actor creation
    # ------------------------------------------------------------------

    def create_actor(
        self,
        role: str,
        session_scope: str,
        variant: str = _DEFAULT_VARIANT,
    ) -> tuple[ActorContext, Any]:
        """Mint an ActorContext and return the matching agent implementation.

        This is the primary connection point between P18.0 identity and
        P18.1 registry: every lookup yields both an identity token and
        an implementation so routing, tracing, and scoped memory all
        reference the same actor.

        Raises RegistryKeyError when (role, variant) is not registered.
        """
        agent = self.get(role, variant)
        ctx = ActorContext.create(actor_role=role, session_scope=session_scope)
        return ctx, agent

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        entries = ", ".join(
            f"{r!r}:{v!r}" for r, v in sorted(self._store)
        )
        return f"AgentRegistry({entries})"
