"""YAGNI mode: a switch the model hears about in a user turn, never in the system prompt.

A switch mid-session that rewrote the system prompt would change the first bytes of every later
request and cost the provider's cache of the whole history, so the note rides in the turn context of
the next run's opening message instead — once per change, again after a compaction took it away, and
never on the screen as something the operator wrote.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from protocore.contracts.types import COMPACTION_SUMMARY_METADATA_KEY, Message, MessageRole, TextBlock

from daedalus.config import RuntimeConfig, Settings
from daedalus.extensions import commands as slash
from daedalus.extensions.api import build_app
from daedalus.host import prompts
from daedalus.host.session_runner import TENANT, SessionManager
from daedalus.host.transcript_view import message_view
from daedalus.stores.database import Database
from daedalus.transport.telegram.front import yagni_argument
from tests.support.models import model_config


def _text(message: Message) -> str:
    return "".join(b.text for b in message.content_blocks if isinstance(b, TextBlock))


async def _turn(manager: SessionManager, sid: str, words: str) -> Message:
    """Open a turn the way a run does and keep it in the working history, as the run would."""
    state = await manager.get_state(sid)
    assert state is not None
    opened = await manager._with_turn_context(state, Message(role=MessageRole.user, content_blocks=[TextBlock(text=words)]))
    history = list(await manager.sessions.list_messages(sid, TENANT, limit=10_000))
    await manager.sessions.replace_messages(sid, TENANT, [*history, opened, Message(role=MessageRole.assistant, content_blocks=[TextBlock(text="done")])])
    return opened


async def test_the_switch_is_stored_on_the_session(settings: Settings, db: Database) -> None:
    manager = SessionManager(settings, model_config(), db=db)
    await manager.start()
    try:
        state = await manager.create_session("s")
        sid = state.session.id
        assert await manager.set_yagni(sid, True) is True
        stored = await manager.sessions.get(sid, TENANT)
        assert stored is not None and stored.metadata.get("yagni") is True
        await manager.set_yagni(sid, False)
        stored = await manager.sessions.get(sid, TENANT)
        assert stored is not None and "yagni" not in stored.metadata and "yagni" not in state.metadata
    finally:
        await manager.close()


async def test_each_change_is_told_once_and_off_says_the_rules_are_gone(settings: Settings, db: Database) -> None:
    manager = SessionManager(settings, model_config(), db=db)
    await manager.start()
    try:
        state = await manager.create_session("s")
        sid = state.session.id
        quiet = await _turn(manager, sid, "before")
        assert prompts.YAGNI_ON not in _text(quiet) and "daedalus.yagni" not in quiet.metadata

        await manager.set_yagni(sid, True)
        first = await _turn(manager, sid, "fix the bug")
        assert _text(first).count(prompts.YAGNI_RULES) == 1 and first.metadata["daedalus.yagni"] == "on"
        # The note is the runtime's, in the turn context; the operator's words are untouched.
        assert prompts.without_turn_context(_text(first)) == "fix the bug"
        again = await _turn(manager, sid, "and the next one")
        assert prompts.YAGNI_ON not in _text(again) and "daedalus.yagni" not in again.metadata

        await manager.set_yagni(sid, False)
        off = await _turn(manager, sid, "carry on")
        assert prompts.YAGNI_OFF in _text(off) and "no longer apply" in _text(off) and off.metadata["daedalus.yagni"] == "off"
        assert prompts.YAGNI_RULES not in _text(off)
        after = await _turn(manager, sid, "more")
        assert prompts.YAGNI_OFF not in _text(after)

        # Switched on and back off before any turn: the model was never told, so nothing is said.
        await manager.set_yagni(sid, True)
        await manager.set_yagni(sid, False)
        flicker = await _turn(manager, sid, "still here")
        assert prompts.YAGNI_ON not in _text(flicker) and prompts.YAGNI_OFF not in _text(flicker)
    finally:
        await manager.close()


async def test_the_rules_are_restated_after_a_compaction_took_them_away(settings: Settings, db: Database) -> None:
    manager = SessionManager(settings, model_config(), db=db)
    await manager.start()
    try:
        state = await manager.create_session("s")
        sid = state.session.id
        await manager.set_yagni(sid, True)
        await _turn(manager, sid, "first")
        # What a host compaction leaves: a summary (written without the turn context) and the kept tail.
        summary = Message(role=MessageRole.user, content_blocks=[TextBlock(text="<compacted-turn id='auto'>the work so far</compacted-turn>")], metadata={COMPACTION_SUMMARY_METADATA_KEY: True})
        await manager.sessions.replace_messages(sid, TENANT, [summary, Message(role=MessageRole.assistant, content_blocks=[TextBlock(text="ok")])])
        restated = await _turn(manager, sid, "go on")
        assert prompts.YAGNI_RULES in _text(restated) and restated.metadata["daedalus.yagni"] == "on"
        assert prompts.YAGNI_ON not in _text(await _turn(manager, sid, "and on"))
        # A kept tail that still holds the note needs no second telling.
        history = list(await manager.sessions.list_messages(sid, TENANT, limit=10_000))
        await manager.sessions.replace_messages(sid, TENANT, [summary, *history[-4:]])
        assert prompts.YAGNI_ON not in _text(await _turn(manager, sid, "once more"))
        # Off, a compaction has nothing to restate.
        await manager.set_yagni(sid, False)
        await _turn(manager, sid, "off now")
        await manager.sessions.replace_messages(sid, TENANT, [summary])
        quiet = await _turn(manager, sid, "after")
        assert prompts.YAGNI_ON not in _text(quiet) and prompts.YAGNI_OFF not in _text(quiet)
    finally:
        await manager.close()


async def test_the_system_prompt_is_the_same_bytes_either_way(settings: Settings, db: Database) -> None:
    manager = SessionManager(settings, model_config(), db=db)
    await manager.start()
    try:
        state = await manager.create_session("s")
        sid = state.session.id
        plain = await manager._build_engine(state, "run-1")
        await manager.set_yagni(sid, True)
        yagni = await manager._build_engine(state, "run-2")
        assert "\n".join(plain.config.system_prompt_sections).encode() == "\n".join(yagni.config.system_prompt_sections).encode()
        assert not any(prompts.YAGNI_RULES in section for section in yagni.config.system_prompt_sections)
    finally:
        await manager.close()


async def test_the_note_is_never_shown_as_the_operators_words(settings: Settings, db: Database) -> None:
    manager = SessionManager(settings, model_config(), db=db)
    await manager.start()
    try:
        state = await manager.create_session("s")
        await manager.set_yagni(state.session.id, True)
        opened = await _turn(manager, state.session.id, "tidy the parser")
        view = message_view(opened)
        assert view["text"] == "tidy the parser" and view["origin"] == "operator" and view["yagni"] == "on"
        assert message_view(Message(role=MessageRole.user, content_blocks=[TextBlock(text="plain")]))["yagni"] is None
    finally:
        await manager.close()


async def test_the_command_and_the_route_switch_it(settings: Settings, config: RuntimeConfig, db: Database) -> None:
    assert yagni_argument("", False) is True and yagni_argument("", True) is False
    assert yagni_argument(" ON ", False) is True and yagni_argument("off", True) is False and yagni_argument("maybe", False) is None
    manager = SessionManager(settings, config, db=db)
    await manager.start()
    try:
        state = await manager.create_session("s")
        sid = state.session.id
        app = SimpleNamespace(settings=settings, config=config, db=db, manager=manager, front=None, extensions={}, guard=None)
        assert "YAGNI: on" in await slash.run_command(app, sid, "/yagni")  # type: ignore[arg-type]
        assert state.metadata.get("yagni") is True
        assert "usage: /yagni" in await slash.run_command(app, sid, "/yagni sideways")  # type: ignore[arg-type]
        assert "YAGNI: off" in await slash.run_command(app, sid, "/yagni off")  # type: ignore[arg-type]
        api = build_app(app, "tok")  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
            headers = {"X-Daedalus-Token": "tok"}
            response = await client.post(f"/api/sessions/{sid}/yagni", json={"on": True}, headers=headers)
            assert response.status_code == 200 and response.json() == {"yagni": True}
            assert state.metadata.get("yagni") is True
            assert (await client.post("/api/sessions/nope/yagni", json={"on": True}, headers=headers)).status_code == 404
            assert (await client.post(f"/api/sessions/{sid}/yagni", json={}, headers=headers)).status_code == 422
    finally:
        await manager.close()
