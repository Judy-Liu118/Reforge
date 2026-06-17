"""One-shot helper: connect to a server, register every discovered tool."""

from __future__ import annotations

from reforge.runtime.skills.mcp.session import MCPSession
from reforge.runtime.skills.mcp.skill import MCPSkill
from reforge.runtime.skills.registry import SkillRegistry


def discover_and_register(
    registry: SkillRegistry,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    name_prefix: str = "mcp",
) -> tuple[MCPSession, list[MCPSkill]]:
    """Spawn an MCP server, list its tools, register each as a Skill.

    Returns the live MCPSession (caller is responsible for `session.shutdown()`)
    and the list of MCPSkill instances added to the registry.
    """
    session = MCPSession.connect(command, env=env, cwd=cwd)
    skills: list[MCPSkill] = []
    for tool in session.list_tools():
        skill = MCPSkill(session, tool, name_prefix=name_prefix)
        registry.register(skill)
        skills.append(skill)
    return session, skills
