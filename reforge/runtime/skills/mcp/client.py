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

Reads run on a dedicated daemon thread rather than inline. A blocking
`readline()` cannot be given a deadline on Windows (`select` does not accept
pipes there), so the thread parks on the pipe and hands frames to callers
through a queue, which *can* be waited on with a timeout. The thread also keeps
stdout drained while no request is outstanding, so a server pushing
notifications never blocks on a full pipe.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any


class MCPProtocolError(RuntimeError):
    """Raised when the server returns an error or violates the protocol."""


class MCPTimeoutError(MCPProtocolError):
    """Raised when the server does not answer a request within its deadline.

    Deliberately a subclass of MCPProtocolError: existing handlers (notably
    MCPSkill.invoke) already catch that type, so a timeout degrades into a
    failed SkillResult without every call site learning a new exception.
    """


class _Eof:
    """Sentinel queued by the reader thread once stdout reaches EOF."""

    __slots__ = ()


_EOF = _Eof()


class MCPClient:
    """JSON-RPC 2.0 transport over a subprocess's stdin/stdout pipes.

    Thread-safe: a single lock serialises request/response cycles so concurrent
    callers don't interleave bytes on the same pipe, and so a frame queued for
    one caller is never consumed by another.
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        if proc.stdin is None or proc.stdout is None:
            raise ValueError("MCPClient requires a process with both stdin and stdout pipes")
        self._proc = proc
        self._lock = threading.Lock()
        self._next_id = 1
        self._frames: queue.Queue[Any] = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_loop, name="mcp-stdout-reader", daemon=True
        )
        self._reader.start()

    # ------------------------------------------------------------------
    # Request / response
    # ------------------------------------------------------------------

    def request(self, method: str, params: dict | None = None, *, timeout_s: float = 30.0) -> Any:
        """Send a JSON-RPC request and block until the matching response arrives.

        Raises MCPTimeoutError if `timeout_s` elapses first, MCPProtocolError on
        an RPC error response or protocol violation.
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

    def _read_loop(self) -> None:
        """Drain stdout for the process lifetime, queueing well-formed frames.

        Malformed lines are dropped here rather than at the call site — some
        servers emit plain-text logs to stdout, and those must not be mistaken
        for a response nor left to fill the pipe.
        """
        stdout = self._proc.stdout
        assert stdout is not None
        try:
            for line in stdout:
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, dict):
                    self._frames.put(frame)
        except (ValueError, OSError):
            # Stream closed underneath us during shutdown; EOF is signalled below.
            pass
        finally:
            self._frames.put(_EOF)

    def _read_until_id(self, expected_id: int, *, timeout_s: float) -> dict:
        """Pull frames from the reader thread until one matches *expected_id*.

        Discarded without comment: server-side notifications (no id field, so
        they can never match) and late responses to an earlier request that
        already timed out. The latter is why a timeout does not poison the
        session — a stale frame is simply skipped by the next caller.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTimeoutError(
                    f"server did not respond to id={expected_id} within {timeout_s}s"
                )
            try:
                frame = self._frames.get(timeout=remaining)
            except queue.Empty:
                raise MCPTimeoutError(
                    f"server did not respond to id={expected_id} within {timeout_s}s"
                ) from None
            if frame is _EOF:
                # Sticky: every later caller must see EOF too, not a timeout.
                self._frames.put(_EOF)
                returncode = self._proc.poll()
                raise MCPProtocolError(
                    f"server closed stdout before responding to id={expected_id} "
                    f"(returncode={returncode})"
                )
            if frame.get("id") == expected_id:
                return frame

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

    def join_reader(self, timeout: float = 1.0) -> None:
        """Wait for the reader thread to observe EOF and finish.

        Best-effort: the thread is a daemon, so a server that never closes
        stdout cannot hold up interpreter exit.
        """
        self._reader.join(timeout=timeout)
