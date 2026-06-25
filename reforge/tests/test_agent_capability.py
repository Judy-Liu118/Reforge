"""AgentCapability — runtime-level isolation contract tests.

Covers:
  - AgentCapability dataclass (frozen, validation)
  - allows_skill / enforce_skill behaviour
  - Memory-write enforcement (read_only forbids)
  - SkillRegistry capability-aware get / list / names / to_openai_tools
  - BoundSkillRegistry view: pre-bound, filtered, enforced
  - RunnerVerifier and DefaultSynthesizer expose capability attribute
"""

from __future__ import annotations

import dataclasses

import pytest

from reforge.runtime.agents.capability import (
    AgentCapability,
    CapabilityViolation,
    scoped_skills,
    unrestricted,
)
from reforge.runtime.agents.synthesizer import DefaultSynthesizer
from reforge.runtime.agents.verifier import RunnerVerifier
from reforge.runtime.skills import SkillContext, SkillRegistry, SkillResult
from reforge.runtime.skills.protocol import Skill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubSkill:
    """Minimal Skill Protocol-conformant stub."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"stub {name}"
        self.input_schema: dict = {"type": "object", "properties": {}}
        self.prompt_fragment = ""

    def invoke(self, params: dict, ctx: SkillContext) -> SkillResult:
        return SkillResult.ok(output=f"{self.name}-ran")


def _registry_with(*names: str) -> SkillRegistry:
    reg = SkillRegistry()
    for n in names:
        reg.register(_StubSkill(n))
    return reg


# ---------------------------------------------------------------------------
# AgentCapability dataclass
# ---------------------------------------------------------------------------


class TestAgentCapability:
    def test_frozen_dataclass(self) -> None:
        cap = AgentCapability(agent_role="verifier")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cap.agent_role = "other"  # type: ignore[misc]

    def test_default_is_unrestricted(self) -> None:
        cap = AgentCapability(agent_role="planner")
        assert cap.allowed_skills is None
        assert cap.allows_skill("anything")
        assert cap.memory_scope == "full"
        assert cap.max_concurrent == 1

    def test_invalid_concurrent_rejected(self) -> None:
        with pytest.raises(ValueError):
            AgentCapability(agent_role="x", max_concurrent=0)

    def test_allow_list_filters(self) -> None:
        cap = scoped_skills("verifier", {"web_search", "read"})
        assert cap.allows_skill("web_search")
        assert cap.allows_skill("read")
        assert not cap.allows_skill("python_sandbox")

    def test_enforce_skill_raises_on_violation(self) -> None:
        cap = scoped_skills("verifier", {"web_search"})
        cap.enforce_skill("web_search")  # no-op
        with pytest.raises(CapabilityViolation) as excinfo:
            cap.enforce_skill("python_sandbox")
        assert "python_sandbox" in str(excinfo.value)
        assert excinfo.value.agent_role == "verifier"
        assert excinfo.value.action == "skill:python_sandbox"

    def test_read_only_forbids_write(self) -> None:
        cap = AgentCapability(agent_role="synth", memory_scope="read_only")
        with pytest.raises(CapabilityViolation):
            cap.enforce_memory_write()

    def test_full_scope_allows_write(self) -> None:
        cap = AgentCapability(agent_role="verifier", memory_scope="full")
        cap.enforce_memory_write()  # must not raise

    def test_unrestricted_helper(self) -> None:
        cap = unrestricted("planner")
        assert cap.agent_role == "planner"
        assert cap.allowed_skills is None
        assert cap.allows_skill("anything")


# ---------------------------------------------------------------------------
# SkillRegistry — capability-aware lookup
# ---------------------------------------------------------------------------


class TestSkillRegistryCapability:
    def test_get_without_capability_unchanged(self) -> None:
        reg = _registry_with("read", "python_sandbox")
        assert reg.get("read") is not None
        assert reg.get("missing") is None

    def test_get_with_capability_enforces(self) -> None:
        reg = _registry_with("read", "python_sandbox")
        cap = scoped_skills("verifier", {"read"})
        assert reg.get("read", capability=cap) is not None
        with pytest.raises(CapabilityViolation):
            reg.get("python_sandbox", capability=cap)

    def test_list_all_filtered_by_capability(self) -> None:
        reg = _registry_with("read", "grep", "python_sandbox")
        cap = scoped_skills("verifier", {"read", "grep"})
        names = [s.name for s in reg.list_all(capability=cap)]
        assert names == ["read", "grep"]
        assert len(reg.list_all()) == 3  # unrestricted still sees all

    def test_names_filtered_by_capability(self) -> None:
        reg = _registry_with("a", "b", "c")
        cap = scoped_skills("v", {"a", "c"})
        assert reg.names(capability=cap) == ["a", "c"]

    def test_to_openai_tools_filtered(self) -> None:
        reg = _registry_with("read", "python_sandbox")
        cap = scoped_skills("verifier", {"read"})
        tools = reg.to_openai_tools(capability=cap)
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "read"


# ---------------------------------------------------------------------------
# BoundSkillRegistry — view pattern
# ---------------------------------------------------------------------------


class TestBoundSkillRegistry:
    def test_bind_returns_view_with_capability(self) -> None:
        reg = _registry_with("read", "python_sandbox")
        cap = scoped_skills("verifier", {"read"})
        bound = reg.bind(cap)
        assert bound.capability is cap

    def test_bound_get_enforces_implicitly(self) -> None:
        reg = _registry_with("read", "python_sandbox")
        bound = reg.bind(scoped_skills("verifier", {"read"}))
        assert bound.get("read") is not None
        with pytest.raises(CapabilityViolation):
            bound.get("python_sandbox")

    def test_bound_list_filters(self) -> None:
        reg = _registry_with("read", "grep", "python_sandbox")
        bound = reg.bind(scoped_skills("verifier", {"read"}))
        assert [s.name for s in bound.list_all()] == ["read"]
        assert bound.names() == ["read"]
        assert len(bound) == 1

    def test_bound_contains_respects_capability(self) -> None:
        reg = _registry_with("read", "python_sandbox")
        bound = reg.bind(scoped_skills("verifier", {"read"}))
        assert "read" in bound
        assert "python_sandbox" not in bound
        # parent registry is unaffected
        assert "python_sandbox" in reg


# ---------------------------------------------------------------------------
# Agent integration: verifier and synthesizer expose capability
# ---------------------------------------------------------------------------


class TestAgentCapabilityWiring:
    def test_verifier_default_capability_is_unrestricted(self) -> None:
        v = RunnerVerifier(runner_factory=lambda: None)  # type: ignore[arg-type]
        assert v.capability.agent_role == "verifier"
        assert v.capability.allowed_skills is None

    def test_verifier_accepts_custom_capability(self) -> None:
        cap = scoped_skills("verifier", {"web_search"})
        v = RunnerVerifier(runner_factory=lambda: None, capability=cap)  # type: ignore[arg-type]
        assert v.capability is cap
        assert v.capability.allows_skill("web_search")
        assert not v.capability.allows_skill("python_sandbox")

    def test_synthesizer_default_capability(self) -> None:
        s = DefaultSynthesizer()
        assert s.capability.agent_role == "synthesizer"
        assert s.capability.memory_scope == "full"

    def test_synthesizer_can_be_read_only(self) -> None:
        cap = AgentCapability(agent_role="synthesizer", memory_scope="read_only")
        s = DefaultSynthesizer(capability=cap)
        with pytest.raises(CapabilityViolation):
            s.capability.enforce_memory_write()


# ---------------------------------------------------------------------------
# End-to-end demonstration: VerifierAgent restricted to web_search only
# ---------------------------------------------------------------------------


class TestEndToEndIsolation:
    """The story we want to tell on a résumé: agent X cannot call skill Y."""

    def test_restricted_verifier_cannot_execute_sandbox(self) -> None:
        reg = _registry_with("web_search", "python_sandbox", "read")
        verifier_cap = scoped_skills("verifier", {"web_search"})
        bound_view = reg.bind(verifier_cap)

        # The verifier sees only what it is permitted to see.
        assert bound_view.names() == ["web_search"]

        # An attempt to grab python_sandbox via the bound view is blocked at
        # the registry boundary, not the agent's own discipline.
        with pytest.raises(CapabilityViolation) as exc:
            bound_view.get("python_sandbox")
        assert exc.value.agent_role == "verifier"
        assert exc.value.action == "skill:python_sandbox"

    def test_unrestricted_planner_sees_full_catalogue(self) -> None:
        reg = _registry_with("web_search", "python_sandbox", "read")
        planner_cap = unrestricted("planner")
        view = reg.bind(planner_cap)
        assert set(view.names()) == {"web_search", "python_sandbox", "read"}
