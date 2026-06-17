"""AgentCapability — runtime-level isolation contract for multi-agent runtime.

Agents have always been *prompt-level* roles. This module promotes that to
*runtime-level* boundary by attaching a typed capability descriptor that the
SkillRegistry and (optionally) MemorySubstrate consult before granting
access.

Three knobs:

- `allowed_skills`  — explicit allow-list of skill names. `None` means
                      unrestricted (default for permissive capability).
- `memory_scope`    — read_only / scoped / full. Consumed by memory
                      substrate wrappers downstream.
- `max_concurrent`  — soft hint to scheduler; enforced where applicable.

Capability is **declarative only**. Enforcement lives at the boundary
(SkillRegistry, MemorySubstrate wrapper). Agents themselves do not check
their own capability — that would defeat the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MemoryScope = Literal["read_only", "scoped", "full"]


class CapabilityViolation(RuntimeError):
    """Raised by enforcement points when an agent tries to exceed its capability."""

    def __init__(self, agent_role: str, action: str, reason: str) -> None:
        super().__init__(
            f"capability violation: agent_role={agent_role!r} action={action!r} reason={reason}"
        )
        self.agent_role = agent_role
        self.action = action
        self.reason = reason


@dataclass(frozen=True)
class AgentCapability:
    """Declarative isolation envelope attached to one agent instance."""

    agent_role: str
    allowed_skills: frozenset[str] | None = None
    memory_scope: MemoryScope = "full"
    max_concurrent: int = 1
    notes: str = ""

    def __post_init__(self) -> None:
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")

    # ------------------------------------------------------------------

    def allows_skill(self, name: str) -> bool:
        """True when this capability grants access to skill *name*."""
        if self.allowed_skills is None:
            return True
        return name in self.allowed_skills

    def enforce_skill(self, name: str) -> None:
        """Raise CapabilityViolation when *name* is not granted."""
        if not self.allows_skill(name):
            raise CapabilityViolation(
                self.agent_role,
                action=f"skill:{name}",
                reason=f"skill not in allowed_skills={sorted(self.allowed_skills or [])}",
            )

    def enforce_memory_write(self) -> None:
        """Raise CapabilityViolation when this capability is read-only."""
        if self.memory_scope == "read_only":
            raise CapabilityViolation(
                self.agent_role,
                action="memory:write",
                reason="memory_scope=read_only forbids write",
            )


def unrestricted(agent_role: str) -> AgentCapability:
    """Convenience constructor for the permissive default."""
    return AgentCapability(agent_role=agent_role)


def scoped_skills(agent_role: str, skills: frozenset[str] | set[str]) -> AgentCapability:
    """Convenience constructor for an allow-listed agent."""
    return AgentCapability(
        agent_role=agent_role,
        allowed_skills=frozenset(skills),
    )
