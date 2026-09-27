"""The whole of a long tool result can be read to its last line.

What the operator saw: opening a tool result and asking for all of it did not show all of it. The
endpoint behind "show all" returned the placeholder compaction leaves in place of an old result, so
the text was a frame of hashes; and a failed fetch was taken as the whole text and hid the button.

Here a step's result is fifty thousand characters. In a session and in a project orchestrator's
chat, at 1440 px and on a 390 px phone, the step is opened, "show all" is pressed, and the result is
scrolled with the wheel until it stops. The block must hold every character, must scroll rather than
cut, and its last line must end up on the screen inside it. A masked result, whose length the
listing does not know, must offer the same. Copy all and Download must be there.

    cd miniapp && npm run build
    mkdir -p /tmp/app-root && ln -s "$PWD/miniapp/dist" /tmp/app-root/app
    python3 tests/browser/serve_app.py 8163 /tmp/app-root &
    APP_URL=http://127.0.0.1:8163/app python3 tests/browser/check_tool_result_full.py

Exit 0 when the last line is reached everywhere.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_stub import DEFAULT_APP, LONG_RESULT, LONG_RESULT_LAST_LINE, expect_app  # noqa: E402
from check_expand_scroll import worked  # noqa: E402
from check_orchestration_mode import PID, go, serve, stubs  # noqa: E402
from screenshots import S1, UNHANDLED, detail, stub  # noqa: E402

BASE = os.environ.get("APP_URL", DEFAULT_APP)
CHROMIUM = os.environ.get("CHROMIUM", "/usr/local/bin/chromium")
SHOTS = os.environ.get("SHOTS_DIR", "")

PREVIEW = 400


def long_turn(seq: int) -> list[dict]:
    """A turn whose work is one build with a long log, and one read compaction has since masked."""
    at = "2026-09-25T09:00:00Z"
    calls = [
        {"id": "call-long", "name": "Exec", "arguments": {"command": "make build 2>&1"}},
        {"id": "call-masked", "name": "Exec", "arguments": {"command": "cat build.log"}},
    ]
    results = [
        {"id": "call-long", "content": LONG_RESULT[:PREVIEW], "is_error": False, "length": len(LONG_RESULT), "clipped": True},
        {"id": "call-masked", "content": "[The output of Exec (12000 tokens) was masked by compaction; the original is stored.]", "is_error": False, "length": None, "clipped": True},
    ]
    return [
        {"role": "user", "seq": seq, "origin": "operator", "text": "Build it and show me the log.", "thinking": "", "tool_calls": [], "tool_results": [], "created_at": at},
        {"role": "assistant", "seq": seq + 1, "text": "", "thinking": "", "tool_calls": calls, "tool_results": [], "created_at": at},
        {"role": "tool", "seq": seq + 2, "text": "", "thinking": "", "tool_calls": [], "tool_results": results, "created_at": at},
        {"role": "assistant", "seq": seq + 3, "text": "The build passed; the log is in the step above.", "thinking": "", "tool_calls": [], "tool_results": [], "created_at": "2026-09-25T09:00:30Z"},
    ]


def open_session(context) -> Page:  # type: ignore[no-untyped-def]
    page = context.new_page()

    def route(r) -> None:  # type: ignore[no-untyped-def]
        path = r.request.url.split("?", 1)[0]
        if r.request.method == "GET" and path.endswith(f"/api/sessions/{S1}"):
            body = detail(S1)
            body["messages"] = worked(10, 6) + long_turn(900)
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        return stub(r)

    page.route("**/api/**", route)
    page.goto(f"{BASE}/agents/{S1}?token=t&scheme=dark&lang=en")
    page.wait_for_selector(".chat-scroll .timeline .thinking-head", timeout=15000)
    return page


def open_orchestrator(context) -> Page:  # type: ignore[no-untyped-def]
    page = context.new_page()
    focus, main = stubs("en")
    held = focus.details["orch-bakery"]
    held["messages"] = held["messages"] + long_turn(5000)
    serve(page, focus, main, "en")
    go(page, f"/orchestration/project/{PID}", "en")
    page.wait_for_selector(".chat-scroll .timeline .thinking-head", timeout=15000)
    return page


# Where the last character of the result is drawn, against the block that holds it and the window.
LAST_LINE = """() => {
  const pre = document.querySelector('.toolcard pre.result.full');
  if (!pre) return null;
  const node = [...pre.childNodes].reverse().find((n) => n.nodeType === 3);
  const range = document.createRange();
  range.setStart(node, node.textContent.length - 1);
  range.setEnd(node, node.textContent.length);
  const line = range.getBoundingClientRect();
  const box = pre.getBoundingClientRect();
  const style = getComputedStyle(pre);
  return {
    length: pre.textContent.length, ends: pre.textContent.slice(-40),
    line: { top: line.top, bottom: line.bottom }, box: { top: box.top, bottom: box.bottom },
    scrollTop: pre.scrollTop, scrollHeight: pre.scrollHeight, clientHeight: pre.clientHeight,
    overflow: style.overflowY, windowHeight: window.innerHeight,
  };
}"""


def read_to_the_end(page: Page, where: str, command: str, label: str) -> list[str]:
    problems: list[str] = []
    heads = page.locator(".chat-scroll .thinking-head")
    last = heads.nth(heads.count() - 1)
    last.scroll_into_view_if_needed()
    if last.get_attribute("aria-expanded") == "false":
        last.click()
    # Two commands in a row are drawn as one "Ran 2 commands" line, which opens into the steps.
    group = page.locator(".chat-scroll .act", has_text="Ran 2 commands").last
    group.wait_for(timeout=15000)
    group.click()
    step = page.locator(".chat-scroll .act", has_text=command).last
    step.wait_for(timeout=15000)
    step.scroll_into_view_if_needed()
    step.click()
    show = page.locator(".toolcard button", has_text=label).last
    show.wait_for(timeout=15000)
    show.click()
    pre = page.locator(".toolcard pre.result.full")
    pre.wait_for(timeout=15000)
    before = page.evaluate(LAST_LINE)
    if before["length"] != len(LONG_RESULT):
        problems.append(f"{where}: the block holds {before['length']} of {len(LONG_RESULT)} characters")
    if not before["ends"].endswith(LONG_RESULT_LAST_LINE):
        problems.append(f"{where}: the text does not end with the result's last line: {before['ends']!r}")
    if before["overflow"] not in ("auto", "scroll") or before["scrollHeight"] <= before["clientHeight"]:
        problems.append(f"{where}: the block does not scroll (overflow {before['overflow']}, {before['scrollHeight']} in {before['clientHeight']})")
    # At once, as a reader does, the wheel brings the block into view and then goes down it until it
    # stops. No pause first: the chat holds a line just clicked in place for a moment, and that hold
    # once pulled the list back under a reader who had started to scroll.
    chat = page.locator(".chat-scroll").bounding_box()
    assert chat
    for _ in range(40):
        box = pre.bounding_box()
        assert box
        middle = box["y"] + min(box["height"], 200) / 2
        if chat["y"] + 40 <= middle <= chat["y"] + chat["height"] - 40:
            break
        page.mouse.move(chat["x"] + chat["width"] / 2, chat["y"] + 20)
        page.mouse.wheel(0, 250 if middle > chat["y"] + chat["height"] / 2 else -250)
        page.wait_for_timeout(40)
    box = pre.bounding_box()
    assert box
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + min(box["height"], 200) / 2)
    # Stopped means three turns in a row that moved nothing: on a loaded machine one turn can land
    # after the reading, and a single still reading ended the run halfway down.
    seen, still = -1.0, 0
    for _ in range(300):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(60)
        now = page.evaluate("() => document.querySelector('.toolcard pre.result.full').scrollTop")
        still = still + 1 if now == seen else 0
        if still >= 3:
            break
        seen = now
    # At its end the block hands the wheel on to the chat, which a reader keeps turning until the
    # end of the block is out from under the composer.
    for _ in range(20):
        edge = page.evaluate("() => { const c = document.querySelector('.chat-scroll').getBoundingClientRect(); const p = document.querySelector('.toolcard pre.result.full').getBoundingClientRect(); return p.bottom <= c.bottom + 1; }")
        if edge:
            break
        page.mouse.wheel(0, 200)
        page.wait_for_timeout(60)
    after = page.evaluate(LAST_LINE)
    line, held = after["line"], after["box"]
    if not (held["top"] - 1 <= line["top"] and line["bottom"] <= held["bottom"] + 1):
        problems.append(f"{where}: after scrolling, the last line ({line['top']:.0f}-{line['bottom']:.0f}) is outside the block ({held['top']:.0f}-{held['bottom']:.0f})")
    if not (0 <= line["top"] and line["bottom"] <= after["windowHeight"]):
        problems.append(f"{where}: after scrolling, the last line ({line['top']:.0f}-{line['bottom']:.0f}) is off the screen of {after['windowHeight']}")
    for tool in ("Copy all", "Download"):
        if page.locator(".toolcard .result-tools button", has_text=tool).count() == 0:
            problems.append(f"{where}: no {tool} beside the whole result")
    print(where, "scrolled", after["scrollTop"], "of", after["scrollHeight"], "last line at", round(line["top"]), "block", round(held["top"]), "-", round(held["bottom"]))
    if SHOTS:
        page.screenshot(path=str(Path(SHOTS) / f"{where.replace(' ', '-')}.png"))
    return problems


def check(context, where: str, opener) -> list[str]:  # type: ignore[no-untyped-def]
    problems: list[str] = []
    for command, label, name in (("make build", "Show all (", "log"), ("cat build.log", "Show the whole result", "masked")):
        page = opener(context)
        page.wait_for_timeout(1200)
        problems += read_to_the_end(page, f"{where} {name}", command, label)
        page.close()
    return problems


def run() -> int:
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        for label, viewport, mobile in (("desktop", {"width": 1440, "height": 900}, False), ("phone", {"width": 390, "height": 844}, True)):
            for chat, opener in (("session", open_session), ("orchestrator", open_orchestrator)):
                context = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile, color_scheme="dark")
                context.add_init_script("try { localStorage.setItem('daedalus.mode', 'orchestration'); } catch (e) {}" if chat == "orchestrator" else "")
                problems += check(context, f"{label} {chat}", opener)
                context.close()
        browser.close()
    print("problems:", problems or "none")
    return 1 if problems else 0


if __name__ == "__main__":
    expect_app(BASE)
    failed = run()
    sys.exit(failed or UNHANDLED.report())
