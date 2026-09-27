"""What the model is shown of the tools: every host tool while it fits, groups held back when it does not.

The core decides the surface; the host decides what it has to decide from. These tests hold the host's
side of it: a registry that ranks (the core's, not its test double), a policy that refuses and never
pins (a pin is what the core never holds back), a tool group for every family worth holding back and
one per connected MCP server, a cap per provider, a prompt that teaches only the tools on the surface,
and the tools a run loaded carried to the next run of the session.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from protocore.contracts.tool_registry import TOOL_VISIBILITY_POLICY_METADATA_KEY, policy_admits
from protocore.contracts.tools import ToolContext
from protocore.runtime.tool_deferral import build_tool_surface, tool_catalogue_block
from protocore.runtime.tool_registry import ToolRegistry

from daedalus.config import (
    STAFF_BLOCKED_TOOLS,
    McpServerConfig,
    ProviderConfig,
    RuntimeConfig,
    Settings,
    max_advertised_tools_for,
)
from daedalus.host import prompts
from daedalus.host.engine_factory import advertised_tools, runtime_constants
from daedalus.host.session_runner import SessionManager
from daedalus.mcp.manager import McpConnection, mcp_group_name, mcp_tool_name
from daedalus.stores.database import Database
from daedalus.tools import TOOL_GROUPS, discover_tools
from tests.support.models import model_config
from tests.support.waiting import until_await
from tests.unit.test_orchestrator import _idle
from tests.unit.test_session_runner import ScriptedProvider

VERBS = ("create", "list", "get", "update", "delete", "search", "archive", "assign", "comment", "export")
OBJECTS = (
    ("issue", "an issue in the tracker"), ("pull_request", "a pull request"), ("calendar_event", "an event in the calendar"),
    ("slack_message", "a message in a Slack channel"), ("invoice", "an invoice for a customer"), ("wiki_page", "a page of the team wiki"),
    ("spreadsheet_row", "a row of a spreadsheet"), ("deployment", "a deployment of a service"), ("dashboard", "a monitoring dashboard"),
    ("contact", "a contact in the address book"), ("ticket", "a support ticket"), ("playlist", "a music playlist"),
    ("folder", "a folder in cloud storage"), ("email_draft", "a draft email"), ("sprint", "a sprint of the board"),
    ("pipeline", "a CI pipeline run"), ("alert", "a monitoring alert"), ("customer", "a customer record"),
    ("order", "an order in the shop"), ("repository", "a code repository"), ("label", "a label of issues"),
    ("webhook", "a webhook subscription"), ("user", "a user account"), ("team", "a team of users"),
    ("document", "a shared document"), ("note", "a personal note"), ("task", "a task of a project"),
    ("release", "a release of a repository"), ("comment", "a comment on a document"), ("reminder", "a reminder"),
)
"""Three hundred tools of one made-up server, named and described the way MCP servers write them."""


def _remote(name: str, description: str) -> SimpleNamespace:
    schema = {"type": "object", "properties": {"id": {"type": "string", "description": "identifier"}}, "required": []}
    return SimpleNamespace(name=name, description=description, input_schema=schema)


def _catalogue(count: int) -> list[SimpleNamespace]:
    remotes = [_remote(f"{verb}_{obj}", f"{verb.title()} {what}. Returns it as JSON.") for obj, what in OBJECTS for verb in VERBS]
    return remotes[:count]


async def _manager(settings: Settings, db: Database, config: RuntimeConfig | None = None, **settings_update: Any) -> SessionManager:
    manager = SessionManager(settings.model_copy(update=settings_update) if settings_update else settings, config or model_config(), db=db)
    await manager.start()

    async def connected(name: str) -> None:
        return None  # the catalogue is published by the test; nothing is spawned

    manager.mcp.ensure = connected  # type: ignore[method-assign, assignment]
    return manager


def _connect(manager: SessionManager, server: str, remotes: list[SimpleNamespace], description: str = "") -> None:
    manager.config.mcp.servers[server] = McpServerConfig(transport="stdio", command="true", description=description)
    manager.mcp.reload(manager.config.mcp.servers)
    connection = McpConnection(server, manager.config.mcp.servers[server])
    manager.mcp._replace_catalog(server, [connection._proxy(remote) for remote in remotes])


def _system(engine: Any) -> str:
    return "\n".join(engine.config.system_prompt_sections)


# -- the registry and the policy ---------------------------------------------------------------------


async def test_both_registries_are_the_cores_ranked_registry_and_only_the_agents_searches(settings: Settings, db: Database) -> None:
    manager = await _manager(settings, db)
    try:
        assert type(manager.tools) is ToolRegistry and type(manager.dispatcher_tools) is ToolRegistry
        assert manager.tools.get("ToolSearch") is not None
        assert manager.dispatcher_tools.get("ToolSearch") is None
        # The ranking is the core's: a Russian message finds the host tool through its hint.
        assert [tool.name for tool in manager.tools.search("подними дев сервер на порту", top_k=1)] == ["ServiceStart"]
        assert {group.name for group in manager.tools.tool_groups()} == set(TOOL_GROUPS)
    finally:
        await manager.close()


async def test_no_session_pins_a_tool_and_the_refusals_are_what_they_were(settings: Settings, db: Database) -> None:
    manager = await _manager(settings, db)
    try:
        ordinary = await manager.create_session("ordinary")
        states = [
            ordinary,
            await manager.create_session("child", metadata={"subagent_of": ordinary.session.id}),
            await manager.create_session("staff", metadata={"staff_session_id": "ss1", "staff_id": "st1"}),
            await manager.create_session("orchestrator", metadata={"orchestrator_of": "p1"}),
            await manager.create_session("voice", metadata={"voice": True}),
            await manager.create_session("main", metadata={"dispatcher": True}),
        ]
        for state in states:
            policy = manager.tool_policy_for(state)
            assert not policy.pinned and not policy.visible, state.session.title
        staff = manager.tool_policy_for(states[2])
        assert set(STAFF_BLOCKED_TOOLS) & {t.name for t in manager.tools.list_all()} <= staff.blocked
        assert policy_admits(staff, "ToolSearch") and policy_admits(staff, "BoardAdd")
        # The roles with an allowlist have nothing to search for, so the search is theirs to refuse.
        for state in (states[3], states[4]):
            assert not policy_admits(manager.tool_policy_for(state), "ToolSearch"), state.session.title
    finally:
        await manager.close()


def test_every_group_of_host_tools_is_declared_and_the_everyday_tools_are_in_none() -> None:
    grouped = {tool.name: getattr(tool, "tool_group", "") for tool in discover_tools()}
    assert {group for group in grouped.values() if group} == set(TOOL_GROUPS)
    # Never held back: what every task needs, and what the models failed to search for when it was.
    for name in ("Read", "Write", "Edit", "MultiEdit", "Find", "Search", "Exec", "WebSearch", "WebFetch", "AskUser", "Skill", "Notify", "HistorySearch"):
        assert not grouped[name], name
    # Held back only by name in a catalogue line that says what the group is for.
    assert grouped["ServiceStart"] == "services" and "ServiceStart" in TOOL_GROUPS["services"]
    assert grouped["IntentCreate"] == "scheduling" and "IntentCreate" in TOOL_GROUPS["scheduling"]


# -- the constants -----------------------------------------------------------------------------------


def test_the_per_message_clip_is_off_and_the_cap_reaches_the_constants() -> None:
    rc = runtime_constants(RuntimeConfig(), context_window=128_000, max_output_tokens=32_000, thinking=True, max_advertised_tools=128)
    assert rc.tool_retrieval_top_k == 0
    assert rc.max_advertised_tools == 128
    assert runtime_constants(RuntimeConfig(), context_window=128_000, max_output_tokens=32_000, thinking=True).max_advertised_tools == 0


def test_the_cap_is_the_providers_setting_or_the_known_limit_of_the_model() -> None:
    assert max_advertised_tools_for(None, "grok-4.7") == 350
    assert max_advertised_tools_for(ProviderConfig(), "google/gemini-3-pro") == 128
    assert max_advertised_tools_for(ProviderConfig(), "deepseek-flash") == 0
    assert max_advertised_tools_for(ProviderConfig(max_advertised_tools=200), "grok-4.7") == 200
    assert max_advertised_tools_for(ProviderConfig(max_advertised_tools=0), "gemini-3-pro") == 0


async def test_a_run_is_capped_by_the_lowest_limit_among_its_rungs(settings: Settings, db: Database) -> None:
    config = model_config()
    config.providers["openrouter"] = config.providers["openrouter"].model_copy(update={"max_advertised_tools": 90})
    manager = await _manager(settings, db, config)
    try:
        def rung(provider_id: str, model: str) -> tuple[Any, str]:
            return SimpleNamespace(endpoint=SimpleNamespace(id=provider_id)), model

        assert manager.advertised_tools_limit([rung("deepseek", "deepseek-flash")]) == 0
        # A fallback that refuses more tools than the primary sends caps the run, or it fails when it is reached.
        assert manager.advertised_tools_limit([rung("deepseek", "deepseek-flash"), rung("openrouter", "x")]) == 90
        assert manager.advertised_tools_limit([rung("deepseek", "grok-4.7"), rung("openrouter", "x")]) == 90
        assert manager.advertised_tools_limit([rung("unknown", "gemini-3")]) == 128
        state = await manager.create_session("capped")
        engine = await manager._build_engine(state, "run-capped")
        assert engine.config.rc.max_advertised_tools == 90  # the test table falls back to openrouter
    finally:
        await manager.close()


# -- a large MCP server ------------------------------------------------------------------------------


async def test_a_large_mcp_server_is_held_back_behind_the_search(settings: Settings, db: Database) -> None:
    manager = await _manager(settings, db)
    try:
        quiet = await manager.create_session("quiet")
        plain = build_tool_surface(await manager._build_engine(quiet, "run-quiet"))
        # With nothing held back the surface is every tool the session may call, and no search.
        admitted = {t.name for t in manager.tools.list_all() if policy_admits(manager.tool_policy_for(quiet), t.name)}
        assert {d.name for d in plain} == admitted - {"ToolSearch"}

        _connect(manager, "bigsrv", _catalogue(300), description="The team's tracker, calendar and chat")
        state = await manager.create_session("big", metadata={"mcp_enabled": ["bigsrv"]})
        engine = await manager._build_engine(state, "run-big")
        surface = [d.name for d in build_tool_surface(engine)]
        assert not [name for name in surface if name.startswith("Mcp_Bigsrv_")]
        assert "ToolSearch" in surface
        assert len(surface) == len(plain) + 1
        assert "- MCP server bigsrv: The team's tracker, calendar and chat. Tools: Mcp_Bigsrv_* (300 tools)" in tool_catalogue_block(engine)
        assert engine._tool_deferral is not None and engine._tool_deferral.deferred_groups == (mcp_group_name("bigsrv"),)
        # The host's own tools stay on the surface, and so does the prompt that teaches them.
        assert "BoardAdd" in surface and prompts.BOARD_TASKS in _system(engine)

        search = manager.tools.get("ToolSearch")
        assert search is not None
        for query in ("create an event in the calendar", "создай событие в календаре"):
            context = ToolContext(tenant_id="daedalus", run_id="r", session_id=state.session.id, metadata={"tool_call_id": "c1", TOOL_VISIBILITY_POLICY_METADATA_KEY: engine.effective_tool_policy})
            result = await search.invoke(context, {"query": query})
            assert mcp_tool_name("bigsrv", "create_calendar_event") in result.metadata["matches"][:3], query

        # A session that did not enable the server is not told of it, nor offered the search.
        other = await manager.create_session("other")
        engine = await manager._build_engine(other, "run-other")
        assert "ToolSearch" not in [d.name for d in build_tool_surface(engine)] and not tool_catalogue_block(engine)
    finally:
        await manager.close()


# -- the prompt --------------------------------------------------------------------------------------


async def test_the_prompt_teaches_only_the_tools_a_role_may_call(settings: Settings, db: Database) -> None:
    manager = await _manager(settings, db)
    try:
        ordinary = await manager.create_session("ordinary")
        system = _system(await manager._build_engine(ordinary, "run-ordinary"))
        for section in (prompts.BOARD_TASKS, prompts.SCHEDULING, prompts.LOOP, prompts.NOTIFY, prompts.HISTORY_SEARCH):
            assert section in system

        staff = await manager.create_session("staff", metadata={"staff_session_id": "ss1", "staff_id": "st1"})
        system = _system(await manager._build_engine(staff, "run-staff"))
        # It may not schedule, loop or notify; it keeps its board, its helpers and its services.
        for section in (prompts.SCHEDULING, prompts.LOOP, prompts.NOTIFY, prompts.BOARD_TWO_WAYS):
            assert section not in system
        for section in (prompts.BOARD_TASKS, prompts.BOARD_SUBAGENT, prompts.BOARD_SERVICES, prompts.HISTORY_HEADLINE):
            assert section in system

        child = await manager.create_session("child", metadata={"subagent_of": ordinary.session.id, "tools_off": ["ScheduleCreate"]})
        system = _system(await manager._build_engine(child, "run-child"))
        assert prompts.NOTIFY not in system and prompts.SCHEDULING not in system and prompts.LOOP in system
    finally:
        await manager.close()


async def test_a_group_held_back_on_a_small_window_leaves_the_prompt_to_the_catalogue(settings: Settings, db: Database, tmp_path: Path) -> None:
    config = model_config()
    for preset in config.presets.values():
        preset.max_output_tokens = 8_000
    manager = await _manager(settings, db, config, browser_container_dir=tmp_path / "browser")
    try:
        state = await manager.create_session("small")
        state.context_window = 40_000
        engine = await manager._build_engine(state, "run-small")
        decision = engine._tool_deferral
        assert decision is not None and {"browser", "scheduling"} <= set(decision.deferred_groups)
        assert "dynamic" not in decision.reasons
        surface = {d.name for d in build_tool_surface(engine)}
        assert "ToolSearch" in surface and "BrowserOpen" not in surface and "ScheduleCreate" not in surface
        assert advertised_tools(engine) == surface
        system = _system(engine)
        assert prompts.BROWSER not in system and prompts.SCHEDULING not in system
        catalogue = tool_catalogue_block(engine)
        assert "- browser: " in catalogue and "BrowserOpen" in catalogue and "ScheduleCreate" in catalogue
        # What never leaves the surface is still taught.
        assert "Exec" in surface and "Notify" in surface and prompts.NOTIFY in system
    finally:
        await manager.close()


# -- what a run loaded, carried to the next ----------------------------------------------------------


async def test_the_tools_a_run_loaded_are_loaded_in_the_next_run_of_the_session(settings: Settings, db: Database) -> None:
    tool = mcp_tool_name("tracker", "create_issue")
    provider = ScriptedProvider([
        {"tool": "ToolSearch", "args": {"query": f"select:{tool}"}},
        {"text": "loaded"},
        {"text": "second"},
    ])
    manager = await _manager(settings, db)
    manager.providers.rungs_for = lambda config, preset=None: [(provider, "scripted-model")]  # type: ignore[method-assign]
    try:
        _connect(manager, "tracker", _catalogue(20))
        state = await manager.create_session("carry", metadata={"mcp_enabled": ["tracker"]})
        sid = state.session.id
        await manager.submit(sid, "load the tracker")
        await until_await(lambda: _idle(manager, sid), "the first run ended")
        assert [t.name for t in provider.requests[1].tools or []][-1] == tool
        assert state.metadata["discovered_tools"] == [tool]
        await until_await(lambda: _stored(manager, sid, [tool]), "the loaded tools were stored")

        await manager.submit(sid, "again")
        await until_await(lambda: _answered_twice(manager, sid), "the second run ended")
        # Loaded from the first request of the second run, after the base surface.
        assert [t.name for t in provider.requests[2].tools or []][-1] == tool

        # What the session may no longer call is not carried back.
        state.metadata["tools_off"] = [tool]
        assert manager.discovered_tools_for(state) == ()
        state.metadata.pop("tools_off")
        state.metadata["mcp_enabled"] = []
        assert manager.discovered_tools_for(state) == ()
    finally:
        await manager.close()


async def _stored(manager: SessionManager, session_id: str, names: list[str]) -> bool:
    session = await manager.sessions.get(session_id, "daedalus")
    return session is not None and session.metadata.get("discovered_tools") == names


async def _answered_twice(manager: SessionManager, session_id: str) -> bool:
    state = manager.live_state(session_id)
    if state is None or state.running or not state.settled.is_set():
        return False
    from protocore.contracts.types import MessageRole

    answers = [m for m in await manager.sessions.list_transcript(session_id) if m.role is MessageRole.assistant]
    return len(answers) >= 3


@pytest.mark.parametrize("names", [["BoardAdd", "Nope", "Exec"], []])
async def test_only_registered_admitted_names_are_carried(settings: Settings, db: Database, names: list[str]) -> None:
    manager = await _manager(settings, db)
    try:
        state = await manager.create_session("s", metadata={"tools_off": ["Exec"]})
        state.metadata["discovered_tools"] = names
        engine = await manager._build_engine(state, "run-s")
        assert engine.config.discovered_tools == (("BoardAdd",) if names else ())
    finally:
        await manager.close()
