"""Host tools.

Every module in this package that defines ``TOOLS`` (a list of ``Tool`` classes or
instances) is picked up by :func:`discover_tools`. Adding a tool is adding a file, and every tool
carries a ``search_hint`` (see :func:`search_hint`).
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from collections.abc import Callable, Iterable
from typing import Any

from protocore.contracts.tools import Tool, ToolContext
from protocore.contracts.types import ToolResult

_ARG_ERROR = re.compile(r"unexpected keyword argument '(?P<extra>\w+)'|missing \d+ required (?:positional|keyword-only) arguments?: (?P<missing>.+)$")


def _explained(exc: TypeError, tool: Tool) -> str | None:
    """A model-readable message for a call with a wrong argument name, or None for any other TypeError."""
    match = _ARG_ERROR.search(str(exc))
    if match is None:
        return None
    params = tool.definition.parameters
    names = sorted((params.properties or {}).keys()) if hasattr(params, "properties") else []
    accepted = ", ".join(names) if names else "see the tool description"
    if match.group("extra"):
        return f"unknown argument {match.group('extra')!r} for {tool.name}; accepted: {accepted}"
    return f"{tool.name} is missing {match.group('missing')}; accepted arguments: {accepted}"


def _guarded(tool: Tool) -> Tool:
    """A call with a misspelled or missing argument answers with the accepted names, not a Python traceback."""
    original = tool.invoke

    async def invoke(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            return await original(context, arguments)
        except TypeError as exc:
            # Only the call boundary is caught: an error raised deeper inside the tool has a real traceback.
            frames = inspect.trace()
            explained = _explained(exc, tool) if len(frames) <= 2 else None
            if explained is None:
                raise
            return ToolResult(tool_call_id=str(context.metadata.get("tool_call_id") or ""), content=explained, is_error=True)

    tool.invoke = invoke  # type: ignore[method-assign]
    return tool


def search_hint(text: str) -> Callable[[type[Tool]], type[Tool]]:
    """Give a ``@tool``-decorated class the words the tool retriever indexes besides its description.

    The core reads ``search_hint`` off the registered object and never sends it to the model, so it can
    carry what a description written for the model should not: Russian words in their dictionary and
    imperative forms, and the slang the operator actually types. Without one, a Russian message shares
    no word with an English description and the tool is not found. Written as a decorator above
    ``@tool`` because that decorator builds the class and offers no place of its own for the attribute.
    """

    def attach(cls: type[Tool]) -> type[Tool]:
        cls.search_hint = text  # type: ignore[attr-defined]
        return cls

    return attach


TOOL_GROUPS: dict[str, str] = {
    "agents": (
        "Other agents: a helper in this workspace (SubAgent, SubAgentSend, SubAgentList), an independent "
        "agent with its own chat and workspace (SpawnAgent), a question to a named peer session (AskPeer)"
    ),
    "board": "The session's task board, the plan of record for long work: tasks with acceptance criteria and checklists",
    "browser": (
        "A real browser the operator can watch and take over: open and read pages, click and type, "
        "hand it over for a sign-in or a payment"
    ),
    "loop": "The standing task of a loop agent: the next wake-up, pause, resume, stop, status",
    "mcp": "MCP servers for this session: list them, switch one on or off, sign in to one that needs OAuth",
    "scheduling": (
        "Work that runs later or on an event: one-shot and recurring schedules (ScheduleCreate) and standing "
        "intents that fire on an inbound webhook or message (IntentCreate)"
    ),
    "self_development": (
        "Changing the agent's own code: a worktree of its repositories, then a pull request or an applied "
        "change, a rebuild or a rollback"
    ),
    "services": (
        "Processes that outlive the turn, such as a dev server or a demo site, on a port the operator can "
        "open (ServiceStart), with their logs"
    ),
}
"""The families of host tools the core may hold back as a unit, and the line its catalogue shows for each.

Advertising every tool is the best surface for as long as it fits, so nothing here is held back on an
ordinary window: a group leaves the surface only when the definitions outgrow the window's share or the
provider's cap on the number of tools, largest group first, and comes back through ToolSearch. The line
is all the model knows of a held-back group, so it says what the group is for and names the tools a
model would otherwise replace with a workaround (a service started from the shell, a schedule instead of
an intent). The file, shell, web, memory and question tools are in no group and never leave."""


def tool_group(name: str) -> Callable[[type[Tool]], type[Tool]]:
    """Put a ``@tool``-decorated class into one of :data:`TOOL_GROUPS`.

    The core reads ``tool_group`` off the registered object, like ``search_hint``; membership never
    reaches the wire. An unknown name fails at import rather than leaving a tool in a group whose
    catalogue line nobody wrote.
    """
    if name not in TOOL_GROUPS:
        raise KeyError(f"unknown tool group {name!r}; declared: {', '.join(sorted(TOOL_GROUPS))}")

    def attach(cls: type[Tool]) -> type[Tool]:
        cls.tool_group = name  # type: ignore[attr-defined]
        return cls

    return attach


def discover_tools() -> list[Tool]:
    """Import every submodule and collect its ``TOOLS``."""
    package = importlib.import_module(__name__)
    found: list[Tool] = []
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        for entry in getattr(module, "TOOLS", ()):
            found.append(_guarded(entry() if isinstance(entry, type) else entry))
    return found


def tool_names(tools: Iterable[Tool]) -> list[str]:
    return sorted(t.name for t in tools)


__all__ = ["TOOL_GROUPS", "discover_tools", "search_hint", "tool_group", "tool_names"]
