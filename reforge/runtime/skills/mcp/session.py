"""MCPSession — manages a live connection to one MCP server.

Owns:
  - the subprocess running the server
  - the initialize → initialized handshake
  - tools/list discovery + caching
  - tools/call dispatch
  - graceful shutdown (close stdin, wait, terminate if needed)

Sessions are expected to live for the duration of a Reforge process. Callers
either construct one explicitly or use `discover_and_register()` which builds
and registers in one shot.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from reforge.runtime.skills.mcp.client import MCPClient, MCPProtocolError

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "reforge", "version": "0.1.0"}
_DEFAULT_SHUTDOWN_TIMEOUT = 5.0


@dataclass(frozen=True)
class MCPToolInfo:
    """One MCP tool as advertised by a server."""

    name: str
    description: str
    input_schema: dict


class MCPSession:
    """A connected MCP server session.

    Use `MCPSession.connect(...)` to spawn + handshake in one call.
    """

    def __init__(self, proc: subprocess.Popen, client: MCPClient) -> None:
        self._proc = proc
        self._client = client
        self._tools_cache: list[MCPToolInfo] | None = None
        self._server_info: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def connect(
        cls,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        initialize_timeout_s: float = 10.0,
    ) -> "MCPSession":
        """Spawn an MCP server and complete the initialize handshake.

        On any handshake failure the subprocess is terminated and the error
        re-raised so callers never get a half-open session.
        """
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            env=env,
            cwd=cwd,
        )
        client = MCPClient(proc)
        session = cls(proc, client)
        try:
            session._handshake(timeout_s=initialize_timeout_s)
        except Exception:
            session.shutdown(force=True)
            raise
        return session

    def _handshake(self, *, timeout_s: float) -> None:
        result = self._client.request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
            timeout_s=timeout_s,
        )
        self._server_info = result.get("serverInfo", {})
        self._client.notify("notifications/initialized")

    # ------------------------------------------------------------------
    # Tool discovery + invocation
    # ------------------------------------------------------------------

    def list_tools(self, *, refresh: bool = False) -> list[MCPToolInfo]:
        """Return tools advertised by the server. Cached after first call."""
        if self._tools_cache is not None and not refresh:
            return self._tools_cache
        result = self._client.request("tools/list")
        tools_raw = result.get("tools", [])
        if not isinstance(tools_raw, list):
            raise MCPProtocolError(f"tools/list returned non-list: {type(tools_raw).__name__}")
        parsed: list[MCPToolInfo] = []
        for t in tools_raw:
            if not isinstance(t, dict) or "name" not in t:
                continue
            parsed.append(
                MCPToolInfo(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
                )
            )
        self._tools_cache = parsed
        return parsed

    def call_tool(self, name: str, arguments: dict, *, timeout_s: float = 30.0) -> dict:
        """Invoke a tool. Returns the raw `result` block from the server.

        The result typically contains `content` (list of content blocks) and
        `isError` (bool). Callers/Skills decide how to surface those.
        """
        return self._client.request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_s=timeout_s,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info or {})

    def shutdown(self, *, timeout_s: float = _DEFAULT_SHUTDOWN_TIMEOUT, force: bool = False) -> None:
        """Close stdin and wait for the server to exit. Terminate if it hangs."""
        self._client.close()
        try:
            self._proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        finally:
            for stream in (self._proc.stdout, self._proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def __enter__(self) -> "MCPSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown(force=exc_type is not None)
