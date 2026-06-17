"""Tests for the Skill abstraction layer (P0).

Covers:
  - Skill Protocol conformance (PythonSandboxSkill structural shape)
  - SkillRegistry: register / get / list / OpenAI tool export
  - PythonSandboxSkill: success path / error path / empty params / SkillContext propagation
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reforge.runtime.skills import (
    Skill,
    SkillContext,
    SkillRegistry,
    SkillResult,
)
from reforge.runtime.skills.builtin import PythonSandboxSkill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(
        session_id="test-session",
        workspace=tmp_path,
        timeout_s=10,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestSkillProtocol:
    def test_python_sandbox_satisfies_skill_protocol(self) -> None:
        skill = PythonSandboxSkill()
        assert isinstance(skill, Skill)

    def test_skill_has_required_attributes(self) -> None:
        skill = PythonSandboxSkill()
        assert isinstance(skill.name, str) and skill.name
        assert isinstance(skill.description, str) and skill.description
        assert isinstance(skill.input_schema, dict)
        assert "properties" in skill.input_schema


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------


class TestSkillRegistry:
    def test_empty_registry(self) -> None:
        reg = SkillRegistry()
        assert len(reg) == 0
        assert reg.get("anything") is None
        assert reg.list_all() == []
        assert reg.names() == []

    def test_register_and_lookup(self) -> None:
        reg = SkillRegistry()
        skill = PythonSandboxSkill()
        reg.register(skill)
        assert len(reg) == 1
        assert "python_sandbox" in reg
        assert reg.get("python_sandbox") is skill
        assert reg.list_all() == [skill]
        assert reg.names() == ["python_sandbox"]

    def test_register_overwrites_existing(self) -> None:
        reg = SkillRegistry()
        first = PythonSandboxSkill()
        second = PythonSandboxSkill()
        reg.register(first)
        reg.register(second)
        assert reg.get("python_sandbox") is second
        assert len(reg) == 1

    def test_to_openai_tools_schema(self) -> None:
        reg = SkillRegistry()
        reg.register(PythonSandboxSkill())
        tools = reg.to_openai_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "python_sandbox"
        assert "description" in tool["function"]
        assert tool["function"]["parameters"]["required"] == ["code"]


# ---------------------------------------------------------------------------
# PythonSandboxSkill
# ---------------------------------------------------------------------------


class TestPythonSandboxSkill:
    def test_successful_execution(self, tmp_path: Path) -> None:
        skill = PythonSandboxSkill()
        result = skill.invoke({"code": "print('hello')"}, _ctx(tmp_path))
        assert isinstance(result, SkillResult)
        assert result.success is True
        assert "hello" in result.output
        assert result.error == ""
        assert result.metadata["exit_code"] == 0
        assert result.duration_ms > 0

    def test_failed_execution_returns_stderr(self, tmp_path: Path) -> None:
        skill = PythonSandboxSkill()
        result = skill.invoke(
            {"code": "raise ValueError('boom')"}, _ctx(tmp_path)
        )
        assert result.success is False
        assert "ValueError" in result.error
        assert result.metadata["exit_code"] != 0

    def test_empty_code_rejected(self, tmp_path: Path) -> None:
        skill = PythonSandboxSkill()
        result = skill.invoke({"code": ""}, _ctx(tmp_path))
        assert result.success is False
        assert "code" in result.error.lower()

    def test_missing_code_param_rejected(self, tmp_path: Path) -> None:
        skill = PythonSandboxSkill()
        result = skill.invoke({}, _ctx(tmp_path))
        assert result.success is False
        assert "code" in result.error.lower()

    def test_skill_context_workspace_used(self, tmp_path: Path) -> None:
        """Code runs in the workspace from SkillContext."""
        skill = PythonSandboxSkill()
        # Write a file in tmp_path and read it via cwd
        (tmp_path / "marker.txt").write_text("workspace_ok", encoding="utf-8")
        code = "print(open('marker.txt').read())"
        result = skill.invoke({"code": code}, _ctx(tmp_path))
        assert result.success is True
        assert "workspace_ok" in result.output

    def test_skill_result_is_frozen(self, tmp_path: Path) -> None:
        skill = PythonSandboxSkill()
        result = skill.invoke({"code": "print(1)"}, _ctx(tmp_path))
        with pytest.raises(Exception):
            # dataclass(frozen=True) raises FrozenInstanceError on set
            result.output = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SkillContext
# ---------------------------------------------------------------------------


class TestSkillContext:
    def test_context_is_frozen(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        with pytest.raises(Exception):
            ctx.session_id = "other"  # type: ignore[misc]

    def test_context_defaults(self, tmp_path: Path) -> None:
        ctx = SkillContext(session_id="s", workspace=tmp_path)
        assert ctx.timeout_s == 30
