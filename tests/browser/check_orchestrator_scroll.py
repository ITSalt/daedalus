"""An orchestrator's chat opens at its end, as every other chat does, and lets go only for the reader.

What the operator saw: the main orchestrator's chat and a project orchestrator's chat opened scrolled to
the middle, every time. Their histories are long enough to be windowed, and the turns take their real
heights only after the first layout; the pin's own scroll event arrived after the history had grown,
was read as the reader scrolling away, and nothing pinned the chat again.

For both chats, at 1440 px and on a 390 px phone, with a history of well over a hundred messages:

- a moment after the chat opens, and again once it has settled, it is at its end;
- scrolled up by a wheel, it stays where the reader put it while the page goes on settling.

    cd miniapp && npm run build
    mkdir -p /tmp/app-root && ln -s "$PWD/miniapp/dist" /tmp/app-root/app
    python3 tests/browser/serve_app.py 8163 /tmp/app-root &
    APP_URL=http://127.0.0.1:8163/app python3 tests/browser/check_orchestrator_scroll.py

Exit 0 when every chat opens at its end and holds the reader's place.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_stub import DEFAULT_APP, expect_app  # noqa: E402
from check_orchestration_mode import PID, go, serve, stubs  # noqa: E402
from screenshots import UNHANDLED  # noqa: E402

BASE = os.environ.get("APP_URL", DEFAULT_APP)
CHROMIUM = os.environ.get("CHROMIUM", "/usr/local/bin/chromium")

# Where the scroller is: its height, how far down it is, and the distance left to the end.
MEASURE = "(() => { const el = document.querySelector('.chat-scroll'); return { height: el.scrollHeight, top: el.scrollTop, gap: el.scrollHeight - el.scrollTop - el.clientHeight }; })()"


def history(detail: dict, pairs: int = 70) -> None:
    """Put a long conversation in front of what the stub already holds: enough turns to be windowed."""
    first = min((m["seq"] for m in detail["messages"]), default=1000)
    earlier = []
    for i in range(pairs):
        seq = first - 2 * (pairs - i)
        earlier.append({"role": "user", "seq": seq, "origin": "operator", "text": f"Question {i}: " + "what about this part " * 8, "thinking": "", "tool_calls": [], "tool_results": [], "created_at": "2026-09-25T08:00:00Z"})
        earlier.append({"role": "assistant", "seq": seq + 1, "text": f"Answer {i}.\n\n" + ("A paragraph of the answer that takes a few lines on any screen. " * 6 + "\n\n") * 3, "thinking": "", "tool_calls": [], "tool_results": [], "created_at": "2026-09-25T08:00:05Z"})
    detail["messages"] = earlier + detail["messages"]


def check(page: Page, where: str, path: str) -> list[str]:
    problems: list[str] = []
    focus, main = stubs("en")
    history(main.detail)
    history(focus.details["orch-bakery"])
    serve(page, focus, main, "en")
    go(page, path, "en")
    page.wait_for_selector(".chat-scroll .timeline [data-slot]", timeout=15000)
    page.wait_for_timeout(300)
    early = page.evaluate(MEASURE)
    page.wait_for_timeout(1500)
    settled = page.evaluate(MEASURE)
    print(where, "early", early, "settled", settled)
    if early["gap"] > 48 or settled["gap"] > 48:
        problems.append(f"{where}: the chat opened {settled['gap']:.0f}px above its end")
    # The reader's own move up is kept while the page goes on settling.
    box = page.locator(".chat-scroll").bounding_box()
    assert box
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, -3000)
    page.wait_for_timeout(1200)
    held = page.evaluate(MEASURE)
    print(where, "after the wheel", held)
    if held["gap"] < 1000:
        problems.append(f"{where}: the chat went back to its end after the reader scrolled up (gap {held['gap']:.0f}px)")
    return problems


def run() -> int:
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        for label, viewport, mobile in (("desktop", {"width": 1440, "height": 900}, False), ("phone", {"width": 390, "height": 844}, True)):
            for chat, path in (("main", "/orchestration"), ("project", f"/orchestration/project/{PID}")):
                context = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile, color_scheme="dark")
                context.add_init_script("try { localStorage.setItem('daedalus.mode', 'orchestration'); } catch (e) {}")
                problems += check(context.new_page(), f"{label} {chat}", path)
                context.close()
        browser.close()
    print("problems:", problems or "none")
    return 1 if problems else 0


if __name__ == "__main__":
    expect_app(BASE)
    failed = run()
    sys.exit(failed or UNHANDLED.report())
