"""A session's Details reports its spend today beside its total: an orchestrator lives for weeks, and
the total alone does not say what it costs now."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from daedalus.config import Settings
from daedalus.extensions.api import build_app
from daedalus.host.session_runner import SessionManager
from daedalus.providers.openai_compat import UsageRecord
from daedalus.stores.database import Database
from tests.support.models import model_config

H = {"X-Daedalus-Token": "tok"}


async def test_the_detail_carries_the_days_share_of_the_spend(settings: Settings, db: Database) -> None:
    manager = SessionManager(settings, model_config(), db=db)
    await manager.start()
    state = await manager.create_session("orchestrator")
    sid = state.session.id
    for tokens, cost in ((1000, 0.5), (300, 0.25)):
        await manager.usage.record(UsageRecord(provider_id="p", model="m", purpose="stream", raw={}, normalized={"input_tokens": tokens, "output_tokens": 10}, cost_usd=cost, duration_ms=1, run_id="r", session_id=sid))
    # The first call was yesterday's: it counts in the total and not in the day.
    await db.execute("UPDATE usage_events SET at = '2000-01-01T00:00:00+00:00' WHERE session_id = ? AND input_tokens = 1000", (sid,))
    app = SimpleNamespace(settings=settings, config=manager.config, db=db, manager=manager, front=None, extensions={})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(app, "tok")), base_url="http://test") as client:  # type: ignore[arg-type]
        usage = (await client.get(f"/api/sessions/{sid}", headers=H)).json()["usage"]
    assert (usage["c"], usage["i"], usage["usd"]) == (2, 1300, 0.75)
    assert (usage["c_today"], usage["i_today"], usage["o_today"], usage["usd_today"]) == (1, 300, 10, 0.25)
    await manager.close()
