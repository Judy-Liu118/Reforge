"""Minimal MCP server used as a test fixture.

Implements the protocol just enough to validate our MCPClient / MCPSession /
MCPSkill stack end-to-end. Exposes two tools:

  - echo(text) : returns the text
  - add(a, b)  : returns a+b
  - boom()     : returns isError=True (for error-path test)

Run as a subprocess with stdio JSON-RPC. Designed to be `python -m` invokable.
"""

from __future__ import annotations

import json
import sys


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
]


def _handle_call(name: str, args: dict) -> dict:
    if name == "echo":
        return {"content": [{"type": "text", "text": args.get("text", "")}]}
    if name == "add":
        a = int(args.get("a", 0))
        b = int(args.get("b", 0))
        return {"content": [{"type": "text", "text": str(a + b)}]}
    if name == "boom":
        return {"content": [{"type": "text", "text": "intentional failure"}], "isError": True}
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
            _ok(req_id, _handle_call(name, args))
        else:
            if req_id is not None:
                _err(req_id, -32601, f"method not found: {method}")


if __name__ == "__main__":
    main()
