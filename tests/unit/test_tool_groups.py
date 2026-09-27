"""The operator's side of the tool groups: the defaults, the settings and a session's own choice, the
month of use the settings page shows, and the routes the app changes them through."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from daedalus.config import RuntimeConfig, Settings
from daedalus.extensions.api import build_app
from daedalus.host import tool_groups
from daedalus.host.session_runner import SessionManager
from daedalus.stores.database import Database
from daedalus.tools import TOOL_GROUPS, discover_tools

H = {"X-Daedalus-Token": "tok"}


def test_the_rare_groups_wait_on_demand_and_the_everyday_ones_stay_while_they_fit() -> None:
    loads = {name: spec.load for name, spec in TOOL_GROUPS.items()}
    assert {name for name, load in loads.items() if load == "lazy"} == {"browser", "self_development", "scheduling", "loop", "docs", "learning", "mcp_oauth"}
    assert {name for name, load in loads.items() if load == "auto"} == {"board", "services", "agents", "mcp"}
    grouped = {tool.name: getattr(tool, "tool_group", "") for tool in discover_tools()}
    # What every session needs is in no group, so nothing can hold it back.
    for name in ("Read", "Write", "Edit", "Exec", "WebFetch", "WebSearch", "AskUser", "Skill", "Notify"):
        assert grouped[name] == "", name
    assert {grouped[n] for n in ("DocsRead", "DocsSearch")} == {"docs"}
    assert {grouped[n] for n in ("LearningReport", "SkillDraft")} == {"learning"}
    assert {grouped[n] for n in ("McpOAuthBegin", "McpOAuthFinish", "McpOAuthStatus", "McpOAuthDisconnect")} == {"mcp_oauth"}
    assert {grouped[n] for n in ("McpList", "McpEnable", "McpDisable")} == {"mcp"}
    # The line the model reads says what the group is for, and the browser's sends plain pages elsewhere.
    assert "WebFetch" in TOOL_GROUPS["browser"].description and "JavaScript" in TOOL_GROUPS["browser"].description


def test_the_settings_name_only_groups_that_exist() -> None:
    config = RuntimeConfig.model_validate({"tools": {"groups": {"browser": {"load": "eager"}}}})
    assert config.tools.groups["browser"].load == "eager"
    with pytest.raises(ValidationError, match="unknown tool group"):
        RuntimeConfig.model_validate({"tools": {"groups": {"telepathy": {"load": "lazy"}}}})
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"tools": {"groups": {"browser": {"load": "sometimes"}}}})


def test_a_session_choice_wins_over_the_settings_which_win_over_the_default() -> None:
    config = RuntimeConfig.model_validate({"tools": {"groups": {"browser": {"load": "eager"}, "board": {"load": "lazy"}}}})
    metadata = {tool_groups.SESSION_LOADS_KEY: {"browser": "lazy", "loop": "eager", "telepathy": "eager", "board": "sometimes"}}
    loads = tool_groups.resolved_loads(config, metadata)
    assert loads["browser"] == "lazy" and tool_groups.load_source("browser", config, metadata) == "session"
    assert loads["loop"] == "eager"
    # An unknown group or mode in the metadata is dropped rather than failing the run.
    assert "telepathy" not in loads and loads["board"] == "lazy" and tool_groups.load_source("board", config, metadata) == "settings"
    assert loads["docs"] == "lazy" and tool_groups.load_source("docs", config, metadata) == "default"
    assert set(loads) == set(TOOL_GROUPS)


def test_use_is_counted_once_per_session_and_run_whichever_of_the_groups_tools_it_called() -> None:
    rows = [
        {"session_id": "s1", "run_id": "r1", "name": "BrowserOpen", "n": 1},
        {"session_id": "s1", "run_id": "r1", "name": "BrowserAct", "n": 5},
        {"session_id": "s1", "run_id": "r2", "name": "BrowserText", "n": 2},
        {"session_id": "s2", "run_id": "r3", "name": "Exec", "n": 9},
        {"session_id": "s3", "run_id": None, "name": "ScheduleCreate", "n": 1},
    ]
    usage = tool_groups.tally(rows, {"BrowserOpen": "browser", "BrowserAct": "browser", "BrowserText": "browser", "ScheduleCreate": "scheduling"}, days=30)
    assert (usage.sessions, usage.runs) == (3, 3)
    assert (usage.groups["browser"].sessions, usage.groups["browser"].runs, usage.groups["browser"].calls) == (1, 2, 8)
    assert (usage.groups["scheduling"].sessions, usage.groups["scheduling"].runs) == (1, 0)
    assert usage.groups["docs"].calls == 0


async def _call(db: Database, session: str, run: str, name: str, at: datetime) -> None:
    await db.execute(
        "INSERT INTO tool_calls(session_id, run_id, tool_call_id, name, at, duration_ms, ok) VALUES (?, ?, ?, ?, ?, 1, 1)",
        (session, run, f"{session}-{run}-{name}-{at.timestamp()}", name, at.isoformat()),
    )


async def test_the_month_of_use_is_read_from_the_calls_and_cached_briefly(db: Database) -> None:
    now = datetime.now(UTC)
    await _call(db, "s1", "r1", "BrowserOpen", now - timedelta(days=2))
    await _call(db, "s1", "r1", "Exec", now - timedelta(days=2))
    await _call(db, "s2", "r2", "Exec", now - timedelta(days=3))
    await _call(db, "s9", "r9", "BrowserOpen", now - timedelta(days=45))  # older than the window
    clock = SimpleNamespace(now=0.0)
    cache = tool_groups.UsageCache(ttl=60, clock=lambda: clock.now)
    usage = await cache.read(db, {"BrowserOpen": "browser"})
    assert (usage.sessions, usage.groups["browser"].sessions, usage.groups["browser"].calls) == (2, 1, 1)
    await _call(db, "s3", "r3", "BrowserOpen", now)
    assert (await cache.read(db, {"BrowserOpen": "browser"})).groups["browser"].sessions == 1  # cached
    clock.now = 61
    assert (await cache.read(db, {"BrowserOpen": "browser"})).groups["browser"].sessions == 2


@pytest.fixture
async def manager(settings: Settings, db: Database) -> Any:
    made = SessionManager(settings, RuntimeConfig(), db=db)
    await made.start()
    yield made
    await made.close()


@pytest.fixture
async def client(settings: Settings, db: Database, manager: SessionManager) -> Any:
    app = SimpleNamespace(settings=settings, config=manager.config, db=db, manager=manager, front=None, extensions={}, guard=None, create_session=manager.create_session)

    async def save_config(config: RuntimeConfig, *, expected_revision: str | None = None) -> None:
        manager.reload_config(config)
        app.config = config

    app.save_config = save_config
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(app, "tok")), base_url="http://test") as c:  # type: ignore[arg-type]
        c.app_state = app  # type: ignore[attr-defined]
        yield c


async def test_the_catalogue_names_each_group_with_its_cost_mode_and_use(client: httpx.AsyncClient, db: Database) -> None:
    await _call(db, "s1", "r1", "SubAgent", datetime.now(UTC))
    body = (await client.get("/api/tool-groups", headers=H)).json()
    by_name = {g["name"]: g for g in body["groups"]}
    # The browser is not installed here, so there is no browser group to choose a mode for.
    assert set(by_name) == set(TOOL_GROUPS) - {"browser"} and body["days"] == 30 and body["sessions"] == 1
    agents = by_name["agents"]
    assert agents["default"] == agents["load"] == "auto" and "SubAgent" in agents["tools"] and agents["tokens"] > 100
    assert agents["usage"] == {"sessions": 1, "runs": 1, "calls": 1}


async def test_a_mode_set_in_settings_is_saved_and_the_default_is_never_written_down(client: httpx.AsyncClient, manager: SessionManager) -> None:
    body = (await client.put("/api/tool-groups/scheduling", json={"load": "eager"}, headers=H)).json()
    assert {g["name"]: g["load"] for g in body["groups"]}["scheduling"] == "eager"
    assert manager.config.tools.groups["scheduling"].load == "eager"
    await client.put("/api/tool-groups/scheduling", json={"load": "lazy"}, headers=H)
    # Clicking the default back removes the entry, so a better default shipped later still arrives.
    assert "scheduling" not in manager.config.tools.groups
    assert (await client.put("/api/tool-groups/telepathy", json={"load": "lazy"}, headers=H)).status_code == 404
    assert (await client.put("/api/tool-groups/scheduling", json={"load": "sometimes"}, headers=H)).status_code == 422


async def test_a_session_chooses_its_own_mode_and_loads_a_group_for_its_next_run(client: httpx.AsyncClient, manager: SessionManager) -> None:
    state = await manager.create_session("groups")
    sid = state.session.id
    groups = {g["name"]: g for g in (await client.get(f"/api/sessions/{sid}/tool-groups", headers=H)).json()["groups"]}
    assert groups["scheduling"]["state"] == "deferred" and groups["scheduling"]["source"] == "default"
    # No run has placed a group that goes by the window yet, so the panel says its mode, not a guess.
    assert groups["board"]["state"] == "undecided" and groups["board"]["load"] == "auto"

    changed = {g["name"]: g for g in (await client.put(f"/api/sessions/{sid}/tool-groups/scheduling", json={"load": "eager"}, headers=H)).json()["groups"]}
    assert changed["scheduling"]["state"] == "advertised" and changed["scheduling"]["source"] == "session"
    assert manager.tool_group_loads_for(state)["scheduling"] == "eager"
    stored = await manager.sessions.get(sid, "daedalus") if hasattr(manager.sessions, "get") else None
    if stored is not None:
        assert stored.metadata[tool_groups.SESSION_LOADS_KEY] == {"scheduling": "eager"}
    back = {g["name"]: g for g in (await client.put(f"/api/sessions/{sid}/tool-groups/scheduling", json={"load": None}, headers=H)).json()["groups"]}
    assert back["scheduling"]["source"] == "default" and tool_groups.SESSION_LOADS_KEY not in state.metadata

    loaded = {g["name"]: g for g in (await client.post(f"/api/sessions/{sid}/tool-groups/loop/load", headers=H)).json()["groups"]}
    assert loaded["loop"]["state"] == "loaded" and loaded["loop"]["pending"]
    assert manager.loaded_groups_for(state) == ("loop",)
    assert (await client.post("/api/sessions/nope/tool-groups/loop/load", headers=H)).status_code == 404
    assert (await client.post(f"/api/sessions/{sid}/tool-groups/telepathy/load", headers=H)).status_code == 404


async def test_a_group_the_session_may_not_call_is_off(client: httpx.AsyncClient, manager: SessionManager) -> None:
    state = await manager.create_session("no loop")
    await manager.set_tools_off(state.session.id, ["LoopNext", "LoopPause", "LoopResume", "LoopStatus", "LoopStop"])
    groups = {g["name"]: g for g in manager.tool_group_states(state)}
    assert groups["loop"]["state"] == "off" and groups["loop"]["tools"] == 0
    # Loading it is asked for and yet brings nothing: the session's policy decides, as for anything carried.
    await manager.load_tool_group_now(state.session.id, "loop")
    assert manager.loaded_groups_for(state) == ()
