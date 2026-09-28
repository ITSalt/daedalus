"""The session endpoint as a client sees it: one page at a time, compressed, with the whole text a request away."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from protocore.contracts.types import (
    CompactionSourceRef,
    Message,
    MessageRole,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from protocore.runtime.events.envelope import TurnEvent
from protocore.runtime.events.types import EventType
from protocore.runtime.wire_format import render_compacted_placeholder
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES
from starlette.requests import Request

from daedalus.config import RuntimeConfig, Settings
from daedalus.extensions.api import build_app
from daedalus.host.session_runner import TENANT, SessionManager
from daedalus.host.transcript_view import TOOL_RESULT_PREVIEW_CHARS
from daedalus.stores.database import Database

H = {"X-Daedalus-Token": "tok"}


@pytest.fixture
async def manager(settings: Settings, db: Database) -> Any:
    made = SessionManager(settings, RuntimeConfig(), db=db)
    await made.start()
    yield made
    await made.close()


@pytest.fixture
async def client(settings: Settings, db: Database, manager: SessionManager) -> Any:
    app = SimpleNamespace(settings=settings, config=manager.config, db=db, manager=manager, front=None, extensions={}, guard=None, create_session=manager.create_session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(app, "tok")), base_url="http://test") as c:  # type: ignore[arg-type]
        yield c


async def _session(manager: SessionManager, count: int) -> str:
    state = await manager.create_session("paged")
    await manager.sessions.append_transcript(
        state.session.id, [Message(role=MessageRole.user, content_blocks=[TextBlock(text=f"turn {i}")]) for i in range(count)]
    )
    return state.session.id


async def test_a_client_walks_back_through_the_session_a_page_at_a_time(client: httpx.AsyncClient, manager: SessionManager) -> None:
    sid = await _session(manager, 40)
    page = (await client.get(f"/api/sessions/{sid}", params={"tail": 10}, headers=H)).json()
    assert [m["text"] for m in page["messages"]] == [f"turn {i}" for i in range(30, 40)]
    assert page["has_older"] and page["first_seq"] == page["messages"][0]["seq"]
    older = (await client.get(f"/api/sessions/{sid}", params={"before": page["first_seq"], "tail": 10}, headers=H)).json()
    assert [m["text"] for m in older["messages"]] == [f"turn {i}" for i in range(20, 30)]
    oldest = (await client.get(f"/api/sessions/{sid}", params={"before": 1, "tail": 10}, headers=H)).json()
    assert oldest["messages"] == [] and not oldest["has_older"]
    whole = (await client.get(f"/api/sessions/{sid}", params={"tail": 999999}, headers=H)).json()
    assert len(whole["messages"]) == 40 and not whole["has_older"]  # a page is capped, not refused


async def test_a_listed_tool_result_is_a_preview_and_the_expand_endpoint_has_the_rest(client: httpx.AsyncClient, manager: SessionManager) -> None:
    state = await manager.create_session("tools")
    sid = state.session.id
    body = "output " * 2000
    await manager.sessions.append_transcript(
        sid,
        [
            Message(role=MessageRole.assistant, content_blocks=[ToolUseBlock(tool_call_id="call-7", name="Exec", arguments_json='{"cmd": "ls"}')]),
            Message(role=MessageRole.tool, content_blocks=[ToolResultBlock(tool_call_id="call-7", content=body)]),
        ],
    )
    page = (await client.get(f"/api/sessions/{sid}", headers=H)).json()
    listed = [r for m in page["messages"] for r in m["tool_results"]]
    assert len(listed) == 1 and len(listed[0]["content"]) == TOOL_RESULT_PREVIEW_CHARS and listed[0]["length"] == len(body)
    full = (await client.get(f"/api/sessions/{sid}/tool-results/call-7", headers=H)).json()
    assert full["content"] == body and full["length"] == len(body)
    assert (await client.get(f"/api/sessions/{sid}/tool-results/nope", headers=H)).status_code == 404


async def _one_result(manager: SessionManager, block: ToolResultBlock) -> str:
    state = await manager.create_session("tools")
    await manager.sessions.append_transcript(
        state.session.id,
        [
            Message(role=MessageRole.assistant, content_blocks=[ToolUseBlock(tool_call_id=block.tool_call_id, name="Read", arguments_json="{}")]),
            Message(role=MessageRole.tool, content_blocks=[block]),
        ],
    )
    return state.session.id


def _masked(ref: str, original: str) -> str:
    """The placeholder compaction leaves: the machine frame, then a line for the model."""
    frame = render_compacted_placeholder(CompactionSourceRef(blob_ref=ref, sha256=ref, original_tokens=len(original) // 4, label="tool_result", tool_name="Read", preview=original[:40]))
    return f"{frame}\n[The output of Read was masked by compaction; the original is stored as {ref}.]"


async def test_a_masked_result_expands_to_the_original_in_the_blob_store(client: httpx.AsyncClient, manager: SessionManager) -> None:
    # The fault this guards: the endpoint returned the placeholder, so "show all" on any result
    # compaction had masked showed a frame of hashes and base64 instead of the output.
    original = "".join(f"line {i:05d} of the original output\n" for i in range(2000))
    stored = await manager.blobs.put(TENANT, original.encode(), content_type="text/plain; charset=utf-8")
    block = ToolResultBlock(tool_call_id="call-m", content=_masked(stored.ref, original), metadata={"compacted": True, "blob_ref": stored.ref})
    sid = await _one_result(manager, block)
    listed = [r for m in (await client.get(f"/api/sessions/{sid}", headers=H)).json()["messages"] for r in m["tool_results"]]
    assert listed[0]["clipped"] and listed[0]["length"] is None
    assert "PROTOCOL_COMPACTED" not in listed[0]["content"] and "masked by compaction" in listed[0]["content"]
    full = (await client.get(f"/api/sessions/{sid}/tool-results/call-m", headers=H)).json()
    assert full["content"] == original and full["length"] == len(original) and full["complete"]
    assert full["content"].endswith("line 01999 of the original output\n")


async def test_a_masked_result_whose_original_is_gone_says_it_is_not_whole(client: httpx.AsyncClient, manager: SessionManager) -> None:
    ref = "0" * 64
    sid = await _one_result(manager, ToolResultBlock(tool_call_id="call-g", content=_masked(ref, "whatever it was"), metadata={"compacted": True, "blob_ref": ref}))
    full = (await client.get(f"/api/sessions/{sid}/tool-results/call-g", headers=H)).json()
    assert not full["complete"] and "PROTOCOL_COMPACTED" not in full["content"] and "masked by compaction" in full["content"]


async def test_an_original_that_is_not_text_is_shown_rather_than_refused(client: httpx.AsyncClient, manager: SessionManager) -> None:
    raw = b"head \xff\xfe\x00 binary \x80 tail"
    stored = await manager.blobs.put(TENANT, raw)
    sid = await _one_result(manager, ToolResultBlock(tool_call_id="call-b", content=_masked(stored.ref, "x"), canonical_ref=stored.ref))
    response = await client.get(f"/api/sessions/{sid}/tool-results/call-b", headers=H)
    assert response.status_code == 200
    body = response.json()
    assert body["complete"] and body["content"].startswith("head ") and body["content"].endswith(" tail") and "\ufffd" in body["content"]


async def test_a_result_cut_for_the_model_expands_to_what_the_tool_returned(client: httpx.AsyncClient, manager: SessionManager) -> None:
    whole = "x" * 9000 + "THE END"
    page = whole[:3000] + f"\n[truncated {len(whole) - 3000} chars]"
    sid = await _one_result(manager, ToolResultBlock(tool_call_id="call-s", content=page, canonical_content=whole))
    listed = [r for m in (await client.get(f"/api/sessions/{sid}", headers=H)).json()["messages"] for r in m["tool_results"]]
    assert listed[0]["length"] == len(whole) and listed[0]["clipped"]
    full = (await client.get(f"/api/sessions/{sid}/tool-results/call-s", headers=H)).json()
    assert full["content"] == whole and full["complete"]


async def test_a_result_of_the_run_still_going_is_found_before_the_transcript_has_it(client: httpx.AsyncClient, manager: SessionManager) -> None:
    # The fault this guards: the transcript is written when a run ends, so "show all" under a step
    # of a running subagent answered "no such tool result" for a result the app had just drawn.
    state = await manager.create_session("running")
    sid = state.session.id
    hold = asyncio.Event()
    state.task = asyncio.create_task(hold.wait())
    try:
        body = "streamed line\n" * 200
        await manager.events.publish_session(sid, TurnEvent(type=EventType.TOOL_RESULT, run_id="run-1", payload={"tool_call_id": "call-live", "is_error": False, "content_blocks": [{"type": "text", "text": body}]}))
        full = (await client.get(f"/api/sessions/{sid}/tool-results/call-live", headers=H)).json()
        assert full["content"] == body and full["length"] == len(body) and full["complete"] and not full.get("pending")
        # The run's own history is read before the stream: it holds the block the transcript will get.
        state.engine = SimpleNamespace(history=[Message(role=MessageRole.tool, content_blocks=[ToolResultBlock(tool_call_id="call-mem", content="from memory")])])  # type: ignore[assignment]
        assert (await client.get(f"/api/sessions/{sid}/tool-results/call-mem", headers=H)).json()["content"] == "from memory"
        # Nowhere yet, while the run goes on: not an error, a note to ask again.
        waiting = await client.get(f"/api/sessions/{sid}/tool-results/call-later", headers=H)
        assert waiting.status_code == 200 and waiting.json()["pending"] and waiting.json()["content"] is None
    finally:
        state.engine = None
        hold.set()
        await state.task
    assert (await client.get(f"/api/sessions/{sid}/tool-results/call-later", headers=H)).status_code == 404


async def test_the_page_goes_out_compressed_and_the_stream_does_not(client: httpx.AsyncClient, manager: SessionManager) -> None:
    sid = await _session(manager, 200)
    response = await client.get(f"/api/sessions/{sid}", headers={**H, "Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"
    assert len(response.content) > 1000  # httpx reports the decoded body; the wire form was smaller
    # The event stream is never compressed: a token must leave the process when it arrives and not
    # when a compression buffer fills. The middleware settles that by content type.
    assert "text/event-stream" in DEFAULT_EXCLUDED_CONTENT_TYPES


async def test_session_stream_pages_to_its_watermark_and_a_fresh_page_skips_old_events(
    settings: Settings,
    db: Database,
    manager: SessionManager,
) -> None:
    sid = await _session(manager, 1)
    app = SimpleNamespace(settings=settings, config=manager.config, db=db, manager=manager, front=None, extensions={}, guard=None, create_session=manager.create_session)
    api = build_app(app, "tok")
    endpoint = next(route.endpoint for route in api.routes if getattr(route, "path", "") == "/api/sessions/{session_id}/stream")

    async def replay(session_id: str, *, after: int, through: int | None = None, limit: int = 500) -> dict[str, Any]:
        assert session_id == sid and limit == 1000
        watermark = 1002
        upper = min(watermark, through if through is not None else watermark, after + limit)
        events = [
            {
                "session_id": sid,
                "run_id": "run",
                "event_seq": seq,
                "history_revision": 0,
                "kind": "tool_started",
                "payload": {"seq": seq},
            }
            for seq in range(after + 1, upper + 1)
        ]
        return {"resync_required": False, "watermark": watermark, "history_revision": 0, "runtime_epoch": "", "events": events}

    manager.events.session_replay = replay  # type: ignore[method-assign]

    async def disconnected() -> dict[str, str]:
        return {"type": "http.disconnect"}

    resumed_request = Request(
        {"type": "http", "method": "GET", "path": f"/api/sessions/{sid}/stream", "query_string": b"after=1", "headers": []},
        disconnected,
    )
    resumed = await endpoint(session_id=sid, request=resumed_request, _={})
    resumed_frames = [chunk async for chunk in resumed.body_iterator]
    assert len(resumed_frames) == 1002  # hello plus every event from 2 through the fixed watermark
    assert resumed_frames[1].startswith("id: 2\n")
    assert resumed_frames[-1].startswith("id: 1002\n")

    fresh_request = Request(
        {"type": "http", "method": "GET", "path": f"/api/sessions/{sid}/stream", "query_string": b"", "headers": []},
        disconnected,
    )
    fresh = await endpoint(session_id=sid, request=fresh_request, _={})
    fresh_frames = [chunk async for chunk in fresh.body_iterator]
    assert len(fresh_frames) == 1 and fresh_frames[0].startswith("event: hello\n")
