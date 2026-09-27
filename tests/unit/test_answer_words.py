"""The operator's answers reach whoever asked in full: every route an answer takes to a model — the
orchestrator's wake-up, a batch from the Questions panel, a refusal's reason, an escalated member's
question, a command-line member's dialog, a replayed question — carries a long, multi-line note
whole. A 600-character cut once handed an orchestrator half of the operator's remarks."""

from __future__ import annotations

import time
from pathlib import Path

from daedalus.config import Settings
from daedalus.extensions import questions
from daedalus.extensions.orchestrator import Orchestrators
from daedalus.harness.contract import Answer
from daedalus.harness.runtime import _settled_reply
from daedalus.host.events import AppEvent
from daedalus.host.wake_queue import Batch, Pending, Wake
from daedalus.stores.database import Database
from tests.support.waiting import until_await
from tests.unit.test_orchestrator import events_messages, last_user_text, rig
from tests.unit.test_staff_runtime import board_task, fake_team

REMARKS = [f"{n}. Замечание номер {n}: анимации не ускорять, надписи не должны налезать на соседние блоки или вылезать за границы своего блока; проверить каждый ключевой кадр перед сдачей." for n in range(1, 21)]
NOTE = "Также замечания по видео:\n" + "\n".join(REMARKS) + "\nИ последнее слово — конец."


def carries_all(text: str) -> bool:
    """Every line of the note, in order and unclipped, and nothing cut with an ellipsis."""
    at = 0
    for line in NOTE.splitlines():
        found = text.find(line, at)
        if found < 0:
            return False
        at = found + len(line)
    return "Такж…" not in text and "конец." in text


def test_the_note_is_long_cyrillic_and_multi_line() -> None:
    assert len(NOTE) >= 2000 and NOTE.count("\n") >= 20


async def test_a_single_answer_with_a_long_note_reaches_the_orchestrator_whole(settings: Settings, db: Database, tmp_path: Path) -> None:
    r = await rig(settings, db, tmp_path, [{"text": "Noted, all of it."}])
    try:
        sid = (await r.orch.enable(r.project.id)).settings.orchestrator.session_id
        await r.call(sid, "ask_operator", title="Какой спокойный трек в ролики", text="Какой трек?", options=["Тёплый", "Холодный"])
        [ask] = await r.manager.asks.open_for(r.project.id, routed_to="operator")
        await r.team.answer(ask.id, selected=["Тёплый"], note=NOTE, by="operator", via="project")

        async def woken() -> bool:
            return bool(await events_messages(r.manager, sid)) and bool(r.provider.requests)

        await until_await(woken, "the answer woke the orchestrator")
        [batch] = await events_messages(r.manager, sid)
        assert f"the operator answered your request [{ask.short_id}]" in batch and "Тёплый — note: Также замечания" in batch
        assert carries_all(batch), batch
        assert carries_all(last_user_text(r.provider, 0)), "the model itself was handed the whole note"
    finally:
        await r.manager.close()


async def test_a_batch_from_the_questions_panel_carries_each_long_note_whole(settings: Settings, db: Database, tmp_path: Path) -> None:
    r = await rig(settings, db, tmp_path, [{"text": "Noted."}])
    try:
        sid = (await r.orch.enable(r.project.id)).settings.orchestrator.session_id
        await r.call(sid, "ask_operator", questions=[{"title": "Track", "text": "Which track?", "options": ["Warm", "Cold"]}, {"title": "Launch", "text": "When?"}])
        track, launch = await r.manager.asks.open_for(r.project.id, routed_to="operator")
        result = await questions.answer(r.team.app, [  # type: ignore[arg-type]
            {"ask_id": track.id, "selected": ["Warm"], "note": NOTE},
            {"ask_id": launch.id, "text": NOTE},
        ], project_id=r.project.id, via="project")
        assert [item["state"] for item in result["results"]] == ["answered", "answered"]

        async def woken() -> bool:
            return bool(await events_messages(r.manager, sid))

        await until_await(woken, "the batch woke the orchestrator")
        [text] = await events_messages(r.manager, sid)
        first, second = text.split(f"[{launch.short_id}]")
        assert carries_all(first) and carries_all(second), text
    finally:
        await r.manager.close()


async def test_a_refusals_reason_and_an_escalated_answer_are_whole_for_the_orchestrator_and_the_member(settings: Settings, db: Database, tmp_path: Path) -> None:
    manager, team, runtime, project = await fake_team(settings, db, tmp_path)
    try:
        ada = await manager.staff.hire(project.id, name="Ada", isolation="shared")
        await team.assign(ada, await board_task(manager, project, "Promo"))
        live = await team.live_of(ada)
        assert live is not None
        question = await team.ingress.question(live, "team:1", "Which track?", ["warm", "cold"])
        await manager.asks.route(question, "operator", "")
        await team.answer(question, selected=["warm"], text="the second one", note=NOTE, by="operator", via="telegram")
        delivered = runtime.answered[-1][2]
        assert delivered.selected == ["warm"] and delivered.text is not None
        assert delivered.text.startswith("the second one — ") and carries_all(delivered.text), "the typed answer and the note both reach the member"

        permission = await team.ingress.permission(live, "req-9", "Bash", "npm publish")
        await team.answer(permission, allow=False, note=NOTE, by="operator", via="app")
        assert carries_all(runtime.answered[-1][2].text or "")

        orch = Orchestrators(team.app)  # type: ignore[arg-type]
        for ask_id in (question, permission):
            ask = await manager.asks.get(ask_id)
            assert ask is not None
            assert carries_all(orch._answer_text(ask)), ask.kind
            line = await orch._answer_line(ask)
            assert "you escalated" in line and carries_all(line)
    finally:
        await manager.close()


async def test_an_answer_is_never_folded_into_the_more_line(settings: Settings, db: Database, tmp_path: Path) -> None:
    r = await rig(settings, db, tmp_path)
    try:
        sid = (await r.orch.enable(r.project.id)).settings.orchestrator.session_id
        await r.call(sid, "ask_operator", title="Track", text="Which track?", options=["Warm", "Cold"])
        [ask] = await r.manager.asks.open_for(r.project.id, routed_to="operator")
        await r.team.answer(ask.id, selected=["Warm"], note=NOTE, by="operator", via="app")
        limit = r.manager.config.orchestrator.batch_max_lines
        news = [AppEvent(i + 1, "2026-01-01T10:00:00+00:00", "run.started", {}, project_id=r.project.id) for i in range(limit + 5)]
        answer = AppEvent(10_000, "2026-01-01T10:05:00+00:00", "ask.answered", {"request_id": ask.id}, project_id=r.project.id)
        batch = Batch(tuple(Pending(e, Wake(str(e.seq)), time.monotonic()) for e in [*news, answer]), urgent=True)
        text = await r.orch.render(r.project.id, batch)
        assert f"answered your request [{ask.short_id}]" in text and carries_all(text)
        assert "- … and 5 more" in text
    finally:
        await r.manager.close()


def test_a_command_line_member_hears_the_option_and_the_words_beside_it() -> None:
    assert Answer("Warm", note=NOTE).said == f"Warm — {NOTE}"
    assert Answer("text", note=NOTE).said == NOTE
    assert Answer("Warm", note="Warm").said == "Warm" and Answer("Warm").said == "Warm"


def test_a_replayed_question_is_told_the_note_as_well() -> None:
    class Settled:
        resolution = {"selected": ["Warm"], "text": "", "note": NOTE, "allow": None}

    said = _settled_reply(Settled())["text"]
    assert said.startswith("Warm — ") and carries_all(said)
