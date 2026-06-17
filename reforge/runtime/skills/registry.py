"""SkillRegistry — name → Skill lookup; OpenAI function-call schema export.

Mirrors AgentRegistry's shape (role → impl lookup) but for skills. No
policy logic lives here; pure registration + lookup, plus capability-aware
views (`bind(capability)`) for runtime-level isolation enforcement.
"""

from __future__ import annotations

from reforge.runtime.agents.capability import AgentCapability
from reforge.runtime.skills.protocol import Skill


class SkillRegistry:
    """In-process registry of available skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register *skill* under its declared name. Overwrites on conflict."""
        self._skills[skill.name] = skill

    def get(self, name: str, *, capability: AgentCapability | None = None) -> Skill | None:
        """Look up a skill by name.

        When *capability* is provided, raises `CapabilityViolation` if the
        skill is not in the agent's allow-list. Returns None when the skill
        is not registered (independent of capability).
        """
        if capability is not None:
            capability.enforce_skill(name)
        return self._skills.get(name)

    def list_all(self, *, capability: AgentCapability | None = None) -> list[Skill]:
        """Return registered skills. Capability-aware view filters to allowed only."""
        if capability is None:
            return list(self._skills.values())
        return [s for s in self._skills.values() if capability.allows_skill(s.name)]

    def names(self, *, capability: AgentCapability | None = None) -> list[str]:
        """Return registered skill names, filtered by capability when present."""
        if capability is None:
            return list(self._skills.keys())
        return [n for n in self._skills.keys() if capability.allows_skill(n)]

    def to_openai_tools(self, *, capability: AgentCapability | None = None) -> list[dict]:
        """Export skills as OpenAI function-call specs, filtered by capability."""
        return [
            {
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": skill.input_schema,
                },
            }
            for skill in self.list_all(capability=capability)
        ]

    def bind(self, capability: AgentCapability) -> "BoundSkillRegistry":
        """Return a restricted view of this registry pre-bound to *capability*."""
        return BoundSkillRegistry(self, capability)

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills


class BoundSkillRegistry:
    """Read-only view of a SkillRegistry pre-bound to one AgentCapability.

    Every method enforces the capability automatically — callers do not need
    to thread the capability through repeatedly. Created by
    `SkillRegistry.bind(capability)`.
    """

    def __init__(self, parent: SkillRegistry, capability: AgentCapability) -> None:
        self._parent = parent
        self._capability = capability

    @property
    def capability(self) -> AgentCapability:
        return self._capability

    def get(self, name: str) -> Skill | None:
        return self._parent.get(name, capability=self._capability)

    def list_all(self) -> list[Skill]:
        return self._parent.list_all(capability=self._capability)

    def names(self) -> list[str]:
        return self._parent.names(capability=self._capability)

    def to_openai_tools(self) -> list[dict]:
        return self._parent.to_openai_tools(capability=self._capability)

    def __len__(self) -> int:
        return len(self.list_all())

    def __contains__(self, name: str) -> bool:
        return self._capability.allows_skill(name) and name in self._parent
