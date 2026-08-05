"""Minimal MCP server used as a test fixture.

Implements the protocol just enough to validate our MCPClient / MCPSession /
MCPSkill stack end-to-end. Exposes five tools:

  - echo(text)      : returns the text
  - add(a, b)       : returns a+b
  - boom()          : returns isError=True (for error-path test)
  - hang()          : never responds (for client-side timeout tests)
  - spew_stderr(kb) : floods stderr, then returns (for drain/deadlock tests)

Two environment switches shape misbehaviour that is otherwise hard to stage:

  REFORGE_TEST_MCP_SWALLOW=<method>  drop that method silently, never answer
  REFORGE_TEST_MCP_LINGER=1          keep running after stdin closes

Run as a subprocess with stdio JSON-RPC. Designed to be `python -m` invokable.
"""

from __future__ import annotations

import json
import os
import sys
import time

# The client spawns us with encoding="utf-8"; match it explicitly so a
# non-UTF-8 platform locale (cp936 here) cannot corrupt the wire.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_SWALLOW = os.environ.get("REFORGE_TEST_MCP_SWALLOW", "")
_LINGER = bool(os.environ.get("REFORGE_TEST_MCP_LINGER"))


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok(req_id, result: dict) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


_TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the text argument unchanged.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two integers and return their sum.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "boom",
        "description": "Always returns isError=True. For testing error paths.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "hang",
        "description": "Never sends a response. For testing client-side timeouts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "spew_stderr",
        "description": "Write kb kilobytes to stderr, then return. For drain tests.",
        "inputSchema": {
            "type": "object",
            "properties": {"kb": {"type": "integer"}},
        },
    },
]


def _spew_stderr(kb: int) -> None:
    line = "x" * 1023 + "\n"
    for _ in range(max(1, kb)):
        sys.stderr.write(line)
    sys.stderr.flush()


def _handle_call(name: str, args: dict) -> dict:
    if name == "echo":
        return {"content": [{"type": "text", "text": args.get("text", "")}]}
    if name == "add":
        a = int(args.get("a", 0))
        b = int(args.get("b", 0))
        return {"content": [{"type": "text", "text": str(a + b)}]}
    if name == "boom":
        return {"content": [{"type": "text", "text": "intentional failure"}], "isError": True}
    if name == "spew_stderr":
        _spew_stderr(int(args.get("kb", 1)))
        return {"content": [{"type": "text", "text": "spew done"}]}
    return {
        "content": [{"type": "text", "text": f"unknown tool: {name}"}],
        "isError": True,
    }


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no id; do not respond.
        if method == "notifications/initialized":
            continue

        # Staged misbehaviour: pretend this method fell into a black hole.
        if _SWALLOW and method == _SWALLOW:
            continue

        if method == "initialize":
            _ok(req_id, {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "reforge-test-server", "version": "0.0.1"},
            })
        elif method == "tools/list":
            _ok(req_id, {"tools": _TOOLS})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            if name == "hang":
                continue  # deliberately never respond
            _ok(req_id, _handle_call(name, args))
        else:
            if req_id is not None:
                _err(req_id, -32601, f"method not found: {method}")

    if _LINGER:
        # Ignore the stdin-closed shutdown signal, forcing the client to
        # escalate. Long enough to outlast any graceful wait under test.
        time.sleep(30)


if __name__ == "__main__":
    main()
