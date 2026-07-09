"""End-to-end tests for MCP integration (P2).

Spawns `reforge/tests/_mcp_test_server.py` as a subprocess and exercises the
full stack: MCPClient ↔ MCPSession ↔ MCPSkill ↔ SkillRegistry.

This is also the demo material: shows that connecting to a real MCP server
takes one line via `discover_and_register()`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from reforge.runtime.skills import Skill, SkillContext, SkillRegistry, SkillResult
from reforge.runtime.skills.mcp import (
    MCPClient,
    MCPProtocolError,
    MCPSession,
    MCPSkill,
    discover_and_register,
)

_SERVER_CMD = [sys.executable, "-m", "reforge.tests._mcp_test_server"]


@pytest.fixture
def session() -> MCPSession:
    s = MCPSession.connect(_SERVER_CMD)
    try:
        yield s
    finally:
        s.shutdown()


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(session_id="mcp-test", workspace=tmp_path, timeout_s=10)


# ---------------------------------------------------------------------------
# MCPSession lifecycle
# ---------------------------------------------------------------------------


class TestMCPSession:
    def test_handshake_populates_server_info(self, session: MCPSession) -> None:
        info = session.server_info
        assert info["name"] == "reforge-test-server"
        assert info["version"] == "0.0.1"

    def test_list_tools_returns_advertised(self, session: MCPSession) -> None:
        tools = session.list_tools()
        names = {t.name for t in tools}
        assert names == {"echo", "add", "boom"}
        echo = next(t for t in tools if t.name == "echo")
        assert echo.input_schema["required"] == ["text"]

    def test_list_tools_caches(self, session: MCPSession) -> None:
        first = session.list_tools()
        second = session.list_tools()
        # Same cached objects (identity, not just equality)
        assert first is second

    def test_call_tool_success(self, session: MCPSession) -> None:
        result = session.call_tool("echo", {"text": "ping"})
        assert result["content"][0]["text"] == "ping"
        assert not result.get("isError")

    def test_call_tool_iserror(self, session: MCPSession) -> None:
        result = session.call_tool("boom", {})
        assert result.get("isError") is True

    def test_context_manager_shutdown(self) -> None:
        with MCPSession.connect(_SERVER_CMD) as s:
            assert s.server_info["name"] == "reforge-test-server"
        # exiting the with block triggers shutdown — process should be dead
        assert s._proc.poll() is not None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# MCPSkill adapts MCP tool to Skill Protocol
# ---------------------------------------------------------------------------


class TestMCPSkill:
    def test_satisfies_skill_protocol(self, session: MCPSession) -> None:
        tool = session.list_tools()[0]
        skill = MCPSkill(session, tool)
        assert isinstance(skill, Skill)

    def test_skill_name_includes_server_label(self, session: MCPSession) -> None:
        echo_tool = next(t for t in session.list_tools() if t.name == "echo")
        skill = MCPSkill(session, echo_tool)
        assert skill.name == "mcp.reforge-test-server.echo"

    def test_skill_invoke_success(self, session: MCPSession, tmp_path: Path) -> None:
        echo_tool = next(t for t in session.list_tools() if t.name == "echo")
        skill = MCPSkill(session, echo_tool)
        result = skill.invoke({"text": "hello mcp"}, _ctx(tmp_path))
        assert isinstance(result, SkillResult)
        assert result.success is True
        assert result.output == "hello mcp"
        assert result.metadata["tool"] == "echo"
        assert result.metadata["server"] == "reforge-test-server"

    def test_skill_invoke_iserror_surfaces_as_failure(
        self, session: MCPSession, tmp_path: Path
    ) -> None:
        boom_tool = next(t for t in session.list_tools() if t.name == "boom")
        skill = MCPSkill(session, boom_tool)
        result = skill.invoke({}, _ctx(tmp_path))
        assert result.success is False
        assert "intentional failure" in result.error

    def test_skill_add(self, session: MCPSession, tmp_path: Path) -> None:
        add_tool = next(t for t in session.list_tools() if t.name == "add")
        skill = MCPSkill(session, add_tool)
        result = skill.invoke({"a": 2, "b": 3}, _ctx(tmp_path))
        assert result.success and result.output == "5"


# ---------------------------------------------------------------------------
# discover_and_register
# ---------------------------------------------------------------------------


class TestDiscoverAndRegister:
    def test_registers_all_tools(self, tmp_path: Path) -> None:
        reg = SkillRegistry()
        session, skills = discover_and_register(reg, _SERVER_CMD)
        try:
            assert len(skills) == 3
            names = set(reg.names())
            assert names == {
                "mcp.reforge-test-server.echo",
                "mcp.reforge-test-server.add",
                "mcp.reforge-test-server.boom",
            }
            # End-to-end via registry lookup
            echo = reg.get("mcp.reforge-test-server.echo")
            r = echo.invoke({"text": "via_registry"}, _ctx(tmp_path))
            assert r.success and r.output == "via_registry"
        finally:
            session.shutdown()

    def test_openai_tools_schema_export_includes_mcp(self) -> None:
        reg = SkillRegistry()
        session, _ = discover_and_register(reg, _SERVER_CMD)
        try:
            tools = reg.to_openai_tools()
            assert any(
                t["function"]["name"] == "mcp.reforge-test-server.add" for t in tools
            )
            add_spec = next(
                t for t in tools if t["function"]["name"] == "mcp.reforge-test-server.add"
            )
            assert add_spec["function"]["parameters"]["required"] == ["a", "b"]
        finally:
            session.shutdown()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_dead_server_raises_protocol_error(self) -> None:
        # /bin/true exits immediately; handshake will fail because no response arrives.
        # Use a python one-liner for portability across OSes.
        with pytest.raises(MCPProtocolError):
            MCPSession.connect([sys.executable, "-c", "import sys; sys.exit(0)"])

    def test_unknown_method_returns_rpc_error(self, session: MCPSession) -> None:
        with pytest.raises(MCPProtocolError) as exc_info:
            session._client.request("nonexistent/method")
        assert "method not found" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# MCPClient unit (no session)
# ---------------------------------------------------------------------------


class TestMCPClientLowLevel:
    def test_client_requires_pipes(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdin=None,
            stdout=None,
        )
        try:
            with pytest.raises(ValueError):
                MCPClient(proc)
        finally:
            proc.wait()

    def test_client_serializes_concurrent_requests(self, session: MCPSession) -> None:
        """Two threads should not interleave JSON on the same pipe."""
        import threading

        results: list[str] = []
        errors: list[Exception] = []

        def hit(tag: str) -> None:
            try:
                r = session.call_tool("echo", {"text": tag})
                results.append(r["content"][0]["text"])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=hit, args=(f"t{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert sorted(results) == ["t0", "t1", "t2", "t3", "t4"]
