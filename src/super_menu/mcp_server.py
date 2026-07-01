"""MCP server that exposes every plugin command as a tool.

Each command becomes an MCP tool named ``<plugin_id>__<command>`` (hyphens in the
plugin id are normalized to underscores) with a JSON schema derived from its
``Param`` list. Run with ``super-menu mcp`` and register it with Claude Code:

    claude mcp add super-menu -- super-menu mcp

Requires the optional ``mcp`` dependency:  ``uv sync --extra mcp``.

Built on the low-level ``mcp.server.Server`` API (stable across SDK versions)
rather than FastMCP internals, since our tools are generated dynamically.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from super_menu.core.registry import default_registry
from super_menu.core.plugin import Command

_TYPE_MAP = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}


def _schema_for(command: Command) -> dict:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in command.params:
        prop: dict[str, Any] = {"type": _TYPE_MAP.get(p.type, "string")}
        if p.help:
            prop["description"] = p.help
        if p.choices:
            prop["enum"] = p.choices
        if p.default is not None:
            prop["default"] = p.default
        props[p.name] = prop
        if p.required:
            required.append(p.name)
    return {"type": "object", "properties": props, "required": required}


def _tool_name(plugin_id: str, command_name: str) -> str:
    return f"{plugin_id.replace('-', '_')}__{command_name}"


def _resolve(tool_name: str):
    """Map a tool name back to (plugin, command)."""
    registry = default_registry()
    for plugin in registry.plugins:
        for command in plugin.commands():
            if _tool_name(plugin.id, command.name) == tool_name:
                return plugin, command
    return None, None


def build_server():
    """Construct and return the configured low-level MCP ``Server``."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server  # noqa: F401 (import-checked here)
        import mcp.types as types
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "The MCP server needs the optional 'mcp' dependency.\n"
            "Install it with:  uv sync --extra mcp"
        ) from exc

    server: "Server" = Server("super-menu")
    registry = default_registry()

    @server.list_tools()
    async def list_tools() -> list["types.Tool"]:
        tools: list[types.Tool] = []
        for plugin in registry.plugins:
            for command in plugin.commands():
                tools.append(types.Tool(
                    name=_tool_name(plugin.id, command.name),
                    description=f"[{plugin.name}] {command.help}",
                    inputSchema=_schema_for(command),
                ))
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list["types.TextContent"]:
        plugin, command = _resolve(name)
        if command is None:
            payload = {"ok": False, "summary": f"unknown tool: {name}"}
        else:
            # command.run is sync and fast; offload to a thread to keep the loop free.
            result = await asyncio.to_thread(command.run, arguments or {})
            payload = result.to_dict()
        return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]

    return server


def run() -> None:
    from mcp.server.stdio import stdio_server

    server = build_server()

    async def _serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_serve())


if __name__ == "__main__":
    run()
