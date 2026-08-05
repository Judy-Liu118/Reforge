"""MCP (Model Context Protocol) integration for Reforge.

MCP is Anthropic's 2024-2025 standard for connecting LLM-driven runtimes to
external tool servers. A server (filesystem, github, postgres, custom) exposes
typed tools over JSON-RPC; clients dynamically discover and invoke them.

This package implements a minimal synchronous stdio MCP client and adapts each
discovered tool into a Reforge `Skill`. The same governor / event log / memory
substrate that govern native skills also govern MCP tool calls — bringing
runtime governance to the MCP ecosystem.

Quick start:
    from reforge.runtime.mcp import discover_and_register
    n = discover_and_register(registry, ["python", "-m", "my_mcp_server"])
    # n tools from the server are now available alongside built-in skills
"""

from reforge.runtime.mcp.client import (
    MCPClient,
    MCPProtocolError,
    MCPTimeoutError,
)
from reforge.runtime.mcp.discovery import discover_and_register
from reforge.runtime.mcp.session import MCPSession, MCPToolInfo
from reforge.runtime.mcp.skill import MCPSkill

__all__ = [
    "MCPClient",
    "MCPProtocolError",
    "MCPSession",
    "MCPSkill",
    "MCPTimeoutError",
    "MCPToolInfo",
    "discover_and_register",
]
