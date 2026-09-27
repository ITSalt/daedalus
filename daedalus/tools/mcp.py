"""MCP servers per session: list, enable, disable."""

from __future__ import annotations

from protocore.contracts.tools import ToolContext
from protocore.contracts.types import ToolResult
from protocore.tools.decorator import tool

from daedalus.tools import search_hint, tool_group
from daedalus.tools._common import error, ok, services_for


def _hook(context: ToolContext):  # type: ignore[no-untyped-def]
    manager = services_for(context).extra.get("manager")
    return manager.service_hooks.get("mcp") if manager is not None else None


@tool_group("mcp")
@search_hint(
    "mcp servers list which integrations connectors external tools available "
    "мсп мцп интеграции коннекторы подключения внешние сервисы какие доступны серверы"
)
@tool(
    name="McpList",
    description=(
        "List the MCP servers configured for this installation, which of them are enabled in "
        "this session, and the tools they provide. Servers are off in a new session; enable "
        "one with McpEnable when its tools would help."
    ),
)
async def mcp_list(context: ToolContext) -> ToolResult:
    hook = _hook(context)
    if hook is None:
        return error(context, "MCP is not available in this session")
    return ok(context, await hook("list", session_id=context.session_id))


@tool_group("mcp")
@search_hint(
    "enable mcp server connect integration turn on connector activate plugin "
    "включить включи подключить подключи мсп интеграцию коннектор активировать активируй плагин"
)
@tool(
    name="McpEnable",
    description=(
        "Enable an MCP server for this session. The connection is made if needed, and the result "
        "names the server's tools exactly (a large one by its exact name prefix, the count and a few "
        "names); call them by those names, and load them with ToolSearch first when the result says "
        "they are not loaded."
    ),
)
async def mcp_enable(context: ToolContext, server: str) -> ToolResult:
    hook = _hook(context)
    if hook is None:
        return error(context, "MCP is not available in this session")
    try:
        return ok(context, await hook("enable", session_id=context.session_id, server=server))
    except (KeyError, RuntimeError) as exc:
        return error(context, str(exc))


@tool_group("mcp")
@search_hint(
    "disable mcp server turn off integration disconnect connector deactivate "
    "выключить выключи отключить отключи мсп интеграцию коннектор деактивировать убрать убери"
)
@tool(name="McpDisable", description="Disable an MCP server for this session; its tools, including any loaded earlier, disappear on the next step.")
async def mcp_disable(context: ToolContext, server: str) -> ToolResult:
    hook = _hook(context)
    if hook is None:
        return error(context, "MCP is not available in this session")
    return ok(context, await hook("disable", session_id=context.session_id, server=server))


TOOLS = [mcp_list, mcp_enable, mcp_disable]

__all__ = ["TOOLS"]
