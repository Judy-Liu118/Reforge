"""End-to-end tests for MCP integration (P2).

Spawns `reforge/tests/_mcp_test_server.py` as a subprocess and exercises the
full stack: MCPClient ↔ MCPSession ↔ MCPSkill ↔ SkillRegistry.

This is also the demo material: shows that connecting to a real MCP server
takes one line via `discover_and_register()`.
"""

from __future__ import annotations

import faulthandler
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from reforge.runtime.skills import Skill, SkillContext, SkillRegistry, SkillResult
from reforge.runtime.mcp import (
    MCPClient,
    MCPProtocolError,
    MCPSession,
    MCPSkill,
    MCPTimeoutError,
    discover_and_register,
)

_SERVER_CMD = [sys.executable, "-m", "reforge.tests._mcp_test_server"]

# The server subprocess must import `reforge` even when pytest has chdir'd
# into a tmp dir and the package is not pip-installed — prepend the repo
# root to PYTHONPATH so the spawn works from a bare checkout.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_ENV = {
    **os.environ,
    "PYTHONPATH": str(_REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
}


@pytest.fixture
def session() -> MCPSession:
    s = MCPSession.connect(_SERVER_CMD, env=_SERVER_ENV)
    try:
        yield s
    finally:
        s.shutdown()


def _ctx(tmp_path: Path) -> SkillContext:
    return SkillContext(session_id="mcp-test", workspace=tmp_path, timeout_s=10)


@pytest.fixture
def watchdog():
    """Process-level deadline for tests that deliberately hang a server.

    If the timeout wiring regresses, these tests block forever on a pipe read —
    a state no Python-level timer can interrupt. faulthandler dumps every
    thread's traceback and exits the process. It is the stdlib stand-in for
    pytest-timeout, which is not a project dependency.

    When it fires, expect NO OUTPUT AT ALL: `exit=True` calls `_exit()`, which
    skips the flush of pytest's fd-level capture buffer, so both the dump and
    the test report are lost. The symptom is a bare `rc=1` after ~60s with
    empty stdout and stderr. Re-run the single test with `-s` to disable
    capture and the traceback appears — that is how the blocking `readline()`
    was pinned down when this fixture was written.
    """
    faulthandler.dump_traceback_later(60.0, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def _env(**extra: str) -> dict[str, str]:
    return {**_SERVER_ENV, **extra}


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
        assert names == {"echo", "add", "boom", "hang", "spew_stderr"}
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
        with MCPSession.connect(_SERVER_CMD, env=_SERVER_ENV) as s:
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
        session, skills = discover_and_register(reg, _SERVER_CMD, env=_SERVER_ENV)
        try:
            assert len(skills) == 5
            names = set(reg.names())
            assert names == {
                "mcp.reforge-test-server.echo",
                "mcp.reforge-test-server.add",
                "mcp.reforge-test-server.boom",
                "mcp.reforge-test-server.hang",
                "mcp.reforge-test-server.spew_stderr",
            }
            # End-to-end via registry lookup
            echo = reg.get("mcp.reforge-test-server.echo")
            r = echo.invoke({"text": "via_registry"}, _ctx(tmp_path))
            assert r.success and r.output == "via_registry"
        finally:
            session.shutdown()

    def test_openai_tools_schema_export_includes_mcp(self) -> None:
        reg = SkillRegistry()
        session, _ = discover_and_register(reg, _SERVER_CMD, env=_SERVER_ENV)
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


# ---------------------------------------------------------------------------
# Timeouts — the wiring that `timeout_s` used to promise but never delivered
# ---------------------------------------------------------------------------


class TestTimeouts:
    def test_call_tool_times_out(self, session: MCPSession, watchdog) -> None:
        start = time.monotonic()
        with pytest.raises(MCPTimeoutError):
            session.call_tool("hang", {}, timeout_s=0.5)
        assert time.monotonic() - start < 5.0

    def test_timeout_is_catchable_as_protocol_error(
        self, session: MCPSession, watchdog
    ) -> None:
        """MCPSkill.invoke only catches MCPProtocolError; timeouts must land there."""
        with pytest.raises(MCPProtocolError):
            session.call_tool("hang", {}, timeout_s=0.5)

    def test_session_still_usable_after_timeout(
        self, session: MCPSession, watchdog
    ) -> None:
        """A timed-out request must not poison the session for later callers."""
        with pytest.raises(MCPTimeoutError):
            session.call_tool("hang", {}, timeout_s=0.5)
        result = session.call_tool("echo", {"text": "after-timeout"})
        assert result["content"][0]["text"] == "after-timeout"

    def test_timeout_does_not_wedge_other_threads(
        self, session: MCPSession, watchdog
    ) -> None:
        """The lock is held across the blocking read — a hang must not be forever."""
        errors: list[Exception] = []
        echoed: list[str] = []

        def hang_caller() -> None:
            try:
                session.call_tool("hang", {}, timeout_s=1.0)
            except MCPTimeoutError:
                pass
            except Exception as exc:  # pragma: no cover - unexpected
                errors.append(exc)

        def echo_caller() -> None:
            try:
                r = session.call_tool("echo", {"text": "unblocked"}, timeout_s=10.0)
                echoed.append(r["content"][0]["text"])
            except Exception as exc:  # pragma: no cover - unexpected
                errors.append(exc)

        t1 = threading.Thread(target=hang_caller)
        t2 = threading.Thread(target=echo_caller)
        start = time.monotonic()
        t1.start()
        time.sleep(0.1)  # ensure the hang acquires the lock first
        t2.start()
        t1.join(timeout=15.0)
        t2.join(timeout=15.0)

        assert not t1.is_alive() and not t2.is_alive()
        assert not errors
        assert echoed == ["unblocked"]
        assert time.monotonic() - start < 15.0

    def test_handshake_timeout(self, watchdog) -> None:
        """initialize_timeout_s must actually bound the handshake."""
        start = time.monotonic()
        with pytest.raises(MCPTimeoutError):
            MCPSession.connect(
                _SERVER_CMD,
                env=_env(REFORGE_TEST_MCP_SWALLOW="initialize"),
                initialize_timeout_s=0.5,
            )
        assert time.monotonic() - start < 5.0

    def test_list_tools_timeout(self, watchdog) -> None:
        """The fourth dead call site: tools/list had no timeout channel at all."""
        s = MCPSession.connect(
            _SERVER_CMD, env=_env(REFORGE_TEST_MCP_SWALLOW="tools/list")
        )
        try:
            start = time.monotonic()
            with pytest.raises(MCPTimeoutError):
                s.list_tools(timeout_s=0.5)
            assert time.monotonic() - start < 5.0
        finally:
            s.shutdown(force=True)

    def test_skill_invoke_timeout_becomes_failed_result(
        self, session: MCPSession, tmp_path: Path, watchdog
    ) -> None:
        """context.timeout_s must reach the wire and degrade into a SkillResult."""
        hang_tool = next(t for t in session.list_tools() if t.name == "hang")
        skill = MCPSkill(session, hang_tool)
        ctx = SkillContext(session_id="mcp-test", workspace=tmp_path, timeout_s=1)
        start = time.monotonic()
        result = skill.invoke({}, ctx)
        assert result.success is False
        assert "hang" in result.error
        assert time.monotonic() - start < 5.0


# ---------------------------------------------------------------------------
# stderr drain
# ---------------------------------------------------------------------------


class TestStderrDrain:
    def test_stderr_flood_does_not_deadlock(self, session: MCPSession, watchdog) -> None:
        """512 KB dwarfs any pipe buffer; without a drain the server blocks."""
        result = session.call_tool("spew_stderr", {"kb": 512}, timeout_s=20.0)
        assert "spew done" in result["content"][0]["text"]
        # And the session survives it.
        alive = session.call_tool("echo", {"text": "still alive"})
        assert alive["content"][0]["text"] == "still alive"

    def test_handshake_error_carries_stderr_tail(self, watchdog) -> None:
        """A crashing server's diagnostics must reach the caller."""
        cmd = [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('BOOM diagnostic line\\n'); "
            "sys.stderr.flush(); sys.exit(3)",
        ]
        with pytest.raises(MCPProtocolError) as exc_info:
            MCPSession.connect(cmd)
        assert "BOOM diagnostic line" in str(exc_info.value)


# ---------------------------------------------------------------------------
# shutdown(force=...) — previously accepted and ignored
# ---------------------------------------------------------------------------


class TestForceShutdown:
    def test_force_skips_the_graceful_wait(self, watchdog) -> None:
        s = MCPSession.connect(_SERVER_CMD, env=_env(REFORGE_TEST_MCP_LINGER="1"))
        start = time.monotonic()
        s.shutdown(force=True)
        elapsed = time.monotonic() - start
        assert s._proc.poll() is not None
        assert elapsed < 2.0, f"force=True still waited {elapsed:.1f}s"

    def test_graceful_shutdown_honours_its_window(self, watchdog) -> None:
        """Counterpart: without force, the wait is really observed."""
        s = MCPSession.connect(_SERVER_CMD, env=_env(REFORGE_TEST_MCP_LINGER="1"))
        start = time.monotonic()
        s.shutdown(timeout_s=1.5)
        elapsed = time.monotonic() - start
        assert s._proc.poll() is not None
        assert elapsed >= 1.5, f"graceful path returned early after {elapsed:.1f}s"

    @pytest.mark.skipif(
        os.name == "nt",
        reason=(
            "POSIX-only: on Windows kill() IS terminate(), TerminateProcess "
            "cannot be ignored, and there are no zombies — the escalation this "
            "asserts is unreachable"
        ),
    )
    def test_kill_escalation_reaps_the_child(self, watchdog) -> None:
        """SIGKILL without wait() leaves a zombie for the process lifetime.

        Asserts on `returncode`, not `poll()`: poll() performs the reap itself,
        so it would report success even with the wait() removed. `returncode`
        is a plain attribute that only wait()/poll() ever sets, so a populated
        value proves shutdown() did the reaping before returning.
        """
        s = MCPSession.connect(
            _SERVER_CMD,
            env=_env(REFORGE_TEST_MCP_LINGER="1", REFORGE_TEST_MCP_IGNORE_SIGTERM="1"),
        )
        s.shutdown(timeout_s=0.5)
        assert s._proc.returncode is not None, "child was killed but never reaped"


# ---------------------------------------------------------------------------
# Non-JSON stdout lines — dropped, but no longer silently
# ---------------------------------------------------------------------------


class TestDroppedStdoutLines:
    def test_plaintext_logs_are_counted_not_just_dropped(self) -> None:
        s = MCPSession.connect(_SERVER_CMD, env=_env(REFORGE_TEST_MCP_STDOUT_NOISE="1"))
        try:
            # The transport still works — noise must not break framing.
            assert s.call_tool("echo", {"text": "ping"})["content"][0]["text"] == "ping"
            assert s._client.dropped_stdout_lines > 0  # type: ignore[attr-defined]
        finally:
            s.shutdown()

    def test_timeout_message_names_the_dropped_lines(self, watchdog) -> None:
        """The payoff: a log-interleaving server no longer reads as a slow one."""
        s = MCPSession.connect(_SERVER_CMD, env=_env(REFORGE_TEST_MCP_STDOUT_NOISE="1"))
        try:
            with pytest.raises(MCPTimeoutError) as exc_info:
                s.call_tool("hang", {}, timeout_s=0.5)
            message = str(exc_info.value)
            assert "non-JSON stdout line" in message
            assert "[reforge-test-server] handling request" in message
        finally:
            s.shutdown(force=True)

    def test_clean_server_adds_no_note(self, session: MCPSession, watchdog) -> None:
        """Counterpart: the suffix must stay absent when nothing was dropped."""
        with pytest.raises(MCPTimeoutError) as exc_info:
            session.call_tool("hang", {}, timeout_s=0.5)
        assert "discarded" not in str(exc_info.value)
