"""MCPSkill — adapter that exposes one MCP tool as a Reforge Skill.

A single MCPSession may discover N tools; each tool is wrapped in its own
MCPSkill instance and registered into the SkillRegistry. From the runtime's
perspective these are indistinguishable from native skills — same Protocol,
same SkillResult, same event/governor treatment.
"""

from __future__ import annotations

import time

from reforge.runtime.skills.context import SkillContext
from reforge.runtime.skills.mcp.client import MCPProtocolError
from reforge.runtime.skills.mcp.session import MCPSession, MCPToolInfo
from reforge.runtime.skills.result import SkillResult


class MCPSkill:
    """A Reforge Skill backed by one MCP-server tool.

    The skill's name defaults to `"mcp.<server>.<tool>"` so multiple servers
    can expose tools with the same local name without colliding.
    """

    def __init__(
        self,
        session: MCPSession,
        tool: MCPToolInfo,
        *,
        name_prefix: str = "mcp",
    ) -> None:
        self._session = session
        self._tool = tool
        server_label = session.server_info.get("name") or "server"
        self._name = f"{name_prefix}.{server_label}.{tool.name}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._tool.description

    @property
    def input_schema(self) -> dict:
        return self._tool.input_schema

    def invoke(self, params: dict, context: SkillContext) -> SkillResult:
        start = time.perf_counter()
        try:
            result = self._session.call_tool(
                self._tool.name, params, timeout_s=float(context.timeout_s)
            )
        except MCPProtocolError as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            return SkillResult(
                success=False,
                error=f"mcp[{self._tool.name}]: {exc}",
                duration_ms=duration_ms,
            )

        is_error = bool(result.get("isError"))
        content_blocks = result.get("content") or []
        text_parts: list[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text", "")
                if isinstance(txt, str):
                    text_parts.append(txt)
        output_text = "\n".join(text_parts)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        return SkillResult(
            success=not is_error,
            output=output_text,
            raw=result,
            error=output_text if is_error else "",
            duration_ms=duration_ms,
            metadata={
                "tool": self._tool.name,
                "server": self._session.server_info.get("name", ""),
                "content_blocks": len(content_blocks),
            },
        )
