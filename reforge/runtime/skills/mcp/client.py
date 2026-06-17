"""Minimal synchronous JSON-RPC 2.0 client over stdio for MCP.

Implements the wire-protocol layer only — lifecycle (initialize / tools/list /
tools/call) lives in `session.py`. This split keeps the transport replaceable:
swapping stdio for HTTP+SSE only touches this file.

Why hand-rolled instead of the official `mcp` SDK:
  - SDK is async; Reforge's Skill.invoke() is sync, so an async bridge would
    leak event-loop concerns into every call site
  - MCP wire protocol is small (JSON-RPC 2.0 + 4 core methods); writing it
    shows understanding rather than dependency
  - One less third-party dependency to vet
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Any


class MCPProtocolError(RuntimeError):
    """Raised when the server returns an error or violates the protocol."""


class MCPClient:
    """JSON-RPC 2.0 transport over a subprocess's stdin/stdout pipes.

    Thread-safe: a single lock serialises request/response cycles so concurrent
    callers don't interleave bytes on the same pipe.
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        if proc.stdin is None or proc.stdout is None:
            raise ValueError("MCPClient requires a process with both stdin and stdout pipes")
        self._proc = proc
        self._lock = threading.Lock()
        self._next_id = 1

    # ------------------------------------------------------------------
    # Request / response
    # ------------------------------------------------------------------

    def request(self, method: str, params: dict | None = None, *, timeout_s: float = 30.0) -> Any:
        """Send a JSON-RPC request and block until the matching response arrives.

        Raises MCPProtocolError on RPC error response or protocol violation.
        """
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
            }
            if params is not None:
                payload["params"] = params
            self._send(payload)
            response = self._read_until_id(req_id, timeout_s=timeout_s)

        if "error" in response:
            err = response["error"]
            raise MCPProtocolError(
                f"{method} failed: {err.get('code')} {err.get('message')}"
            )
        if "result" not in response:
            raise MCPProtocolError(f"{method} response missing 'result' field")
        return response["result"]

    def notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        with self._lock:
            payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            self._send(payload)

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _send(self, payload: dict) -> None:
        assert self._proc.stdin is not None  # narrow for type checker
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPProtocolError(f"failed to write to server stdin: {exc}") from exc

    def _read_until_id(self, expected_id: int, *, timeout_s: float) -> dict:
        """Read JSON-RPC frames until one matches *expected_id*.

        Server-side notifications (no id field) are discarded silently — they're
        progress/log messages that callers don't need at this layer.
        """
        assert self._proc.stdout is not None
        # Block-line reads in subprocess.Popen are already line-buffered when
        # text=True is set on the Popen instance. We rely on the caller to set
        # text=True (the MCPSession constructor does).
        while True:
            line = self._proc.stdout.readline()
            if not line:
                returncode = self._proc.poll()
                raise MCPProtocolError(
                    f"server closed stdout before responding to id={expected_id} "
                    f"(returncode={returncode})"
                )
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines — some servers emit logs to stdout
                continue
            if not isinstance(frame, dict):
                continue
            if frame.get("id") == expected_id:
                return frame
            # otherwise: notification or response for an earlier (cancelled) request

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close stdin to signal shutdown; do NOT terminate the process here."""
        if self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
