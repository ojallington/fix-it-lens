"""The pilot door over the REST surface: invite codes, two households apart, the cap, and the no-HA path."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import icalendar
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.feeds import push
from app.feeds.caps import assert_household_under_cap
from app.feeds.router import Deps, make_router
from app.schedule import store
from calls import Client, load_config
from calls.errors import BudgetExceeded
from calls.ledger import Ledger
from contracts.models.card import Card
from tests.l5.conftest import T0, add_appliance
from tests.l5.test_push import Browser, FakeSession

JOB = "clean_debris_filter"


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock(T0)


@pytest.fixture
def deps(conn: sqlite3.Connection, secret: bytes, loader: Callable[[str], Card | None], clock: Clock,
         tmp_path: Path) -> Deps:
    return Deps(conn, secret, loader, push.load_vapid(tmp_path / "v.pem", create=True), clock,
                invite_failures_per_hour=5)


@pytest.fixture
def api(deps: Deps) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(make_router(deps), prefix="/api/v1")
    with TestClient(app, base_url="https://fixit.test") as c:
        yield c


def ok(sub: push.Subscription, r: object) -> int:
    return 201


def join(api: TestClient, conn: sqlite3.Connection, code: str | None = None) -> tuple[str, dict[str, str]]:
    code = code or store.create_invites(conn, 1, at=T0)[0].code
    r = api.post("/api/v1/households", json={"invite_code": code.lower(), "consent": True, "lang": "en"})
    assert r.status_code == 201, r.text
    return r.json()["id"], {"Authorization": f"Bearer {r.json()['token']}"}


def test_wrong_or_used_code_gets_nothing(api: TestClient, conn: sqlite3.Connection) -> None:
    assert api.post("/api/v1/households", json={"invite_code": "NOPE", "consent": True}).status_code == 403
    [inv] = store.create_invites(conn, 1, at=T0)
    assert api.post("/api/v1/households", json={"invite_code": inv.code, "consent": False}).status_code == 422
    join(api, conn, inv.code)
    r = api.post("/api/v1/households", json={"invite_code": inv.code, "consent": True})
    assert r.status_code == 403 and "token" not in r.text
    assert api.get("/api/v1/due").status_code == 401
    assert api.get("/api/v1/due", headers={"Authorization": f"Bearer {inv.household_id}-0000"}).status_code == 401


def test_bad_codes_are_throttled(api: TestClient) -> None:
    codes = [api.post("/api/v1/households", json={"invite_code": f"X{i}", "consent": True}).status_code
             for i in range(6)]
    assert codes == [403] * 5 + [429]


def test_two_households_see_only_their_own(api: TestClient, conn: sqlite3.Connection, deps: Deps) -> None:
    a, ha = join(api, conn)
    b, hb = join(api, conn)
    aa, ab = add_appliance(conn, a), add_appliance(conn, b)
    assert api.put(f"/api/v1/appliances/{ab}/jobs/{JOB}/last-done", json={"source": "never"},
                   headers=ha).status_code == 404
    assert api.put(f"/api/v1/appliances/{aa}/jobs/{JOB}/last-done", json={"source": "never"},
                   headers=ha).status_code == 200
    assert [d["appliance_id"] for d in api.get("/api/v1/due", headers=ha).json()] == [aa]
    assert [d["appliance_id"] for d in api.get("/api/v1/due", headers=hb).json()] == [ab]
    api.post("/api/v1/push/subscriptions", json=Browser().sub, headers=ha)
    [sent] = push.dispatch(conn, T0, deps.load_card, ok)
    key_a = sent.key
    assert api.post(f"/api/v1/reminders/{key_a}/opened", json={"channel": "push"}, headers=hb).status_code == 404
    feed_a = api.get("/api/v1/household/feeds", headers=ha).json()["ics_url"]
    feed_b = api.get("/api/v1/household/feeds", headers=hb).json()["ics_url"]
    assert feed_a != feed_b
    assert aa in api.get(feed_a).text and ab not in api.get(feed_a).text
    assert api.get(feed_a.replace(a, b)).status_code == 404
    assert api.delete("/api/v1/household", headers=hb).status_code == 204
    assert api.get("/api/v1/household", headers=hb).status_code == 401
    assert api.get("/api/v1/household", headers=ha).json()["id"] == a


def test_nothing_moves_a_due_date_forward_but_the_user(api: TestClient, conn: sqlite3.Connection,
                                                       clock: Clock, deps: Deps) -> None:
    hid, h = join(api, conn)
    aid = add_appliance(conn, hid)
    path = f"/api/v1/appliances/{aid}/jobs/{JOB}/last-done"
    assert api.put(path, json={"source": "user", "date": "2026-10-01"}, headers=h).json()["due_on"] == "2027-01-01"
    browser = Browser()
    seen = []
    for t in (T0, datetime(2027, 1, 1, 8, tzinfo=UTC), datetime(2027, 2, 1, 8, tzinfo=UTC)):
        clock.t = t
        api.get("/api/v1/due", headers=h)
        api.get("/api/v1/due?within_days=0", headers=h)
        api.post("/api/v1/push/subscriptions", json=browser.sub, headers=h)
        push.dispatch(conn, t, deps.load_card, ok)
        api.get(api.get("/api/v1/household/feeds", headers=h).json()["ics_url"])
        key = store.reminder_key(aid, JOB, date(2027, 1, 1))
        api.post(f"/api/v1/reminders/{key}/opened", json={"channel": "push"}, headers=h)
        api.request("DELETE", "/api/v1/push/subscriptions", json={"endpoint": browser.sub["endpoint"]}, headers=h)
        seen.append(api.get("/api/v1/due", headers=h).json()[0]["due_on"])
    assert seen == ["2027-01-01"] * 3  # due, overdue, a month overdue: never moved
    store.record_job_done_checked(conn, hid, aid, JOB, "s1", on=date(2027, 2, 1), at=clock.t)
    assert api.get("/api/v1/due", headers=h).json()[0]["due_on"] == "2027-05-01"
    # The user's own answer is the one door that can bring it sooner.
    assert api.put(path, json={"source": "never"}, headers=h).json()["due_on"] == "2027-02-01"
    assert api.put(path, json={"source": "user", "date": "2027-03-01"}, headers=h).status_code == 422


def test_household_without_home_assistant_runs_the_whole_path(api: TestClient, conn: sqlite3.Connection,
                                                              clock: Clock, deps: Deps) -> None:
    hid, h = join(api, conn)
    aid = add_appliance(conn, hid)  # ha_entities NULL: no Home Assistant at all
    assert api.get("/api/v1/due", headers=h).json()[0]["due_on"] is None   # asks "When did you last do this?"
    d = api.put(f"/api/v1/appliances/{aid}/jobs/{JOB}/last-done", json={"source": "unknown"}, headers=h).json()
    assert d["due_on"] == "2026-10-27" and d["page"] == 3
    clock.t = datetime(2026, 10, 27, 8, 5, tzinfo=UTC)
    # Reminder by push (Android) ...
    browser, session = Browser(), FakeSession()
    assert api.post("/api/v1/push/subscriptions", json=browser.sub, headers=h).status_code == 201
    assert deps.vapid is not None
    [sent] = push.dispatch(conn, clock.t, deps.load_card, push.webpush_sender(deps.vapid, session=session))
    msg = browser.decrypt(session.posts[0][1])
    assert sent.delivered == 1 and msg["footnote"] == "from your manual, p. 3"
    # ... and by .ics (iPhone): a valid calendar with a VALARM.
    cal = icalendar.Calendar.from_ical(api.get(api.get("/api/v1/household/feeds", headers=h).json()["ics_url"]).text)
    [ev] = [c for c in cal.walk("VEVENT")]
    [al] = ev.walk("VALARM")
    assert ev["dtstart"].dt == date(2026, 10, 27) and al["action"] == "DISPLAY"
    assert al["trigger"].dt == datetime(2026, 10, 27, 8, tzinfo=UTC)
    assert "from your manual, p. 3" in str(ev["description"]) and "&ch=ics" in str(ev["url"])
    assert len(cal.walk("VTODO")) == 1
    assert api.post(f"/api/v1/reminders/{msg['key']}/opened", json={"channel": "push"}, headers=h).status_code == 204
    assert api.post(f"/api/v1/reminders/{msg['key']}/opened", json={"channel": "push"}, headers=h).status_code == 204
    store.record_job_started(conn, hid, aid, JOB, "s1", at=clock.t)
    store.record_job_done_checked(conn, hid, aid, JOB, "s1", on=clock.t.date(), at=clock.t)
    assert api.get("/api/v1/due", headers=h).json()[0]["due_on"] == "2027-01-27"
    kinds = [(r["kind"], r["channel"]) for r in conn.execute("SELECT kind, channel FROM pilot_events ORDER BY id")]
    assert kinds == [("reminder_sent", "push"), ("reminder_sent", "ics"), ("reminder_opened", "push"),
                     ("job_started", None), ("job_done_checked", None)]


class NoNetwork:
    name = "api"
    calls = 0

    async def stream(self, body: dict[str, Any], *, sha: str, kind: str) -> Any:
        NoNetwork.calls += 1
        raise AssertionError("a model call was made")
        yield  # pragma: no cover


def test_home_past_its_cap_is_refused_before_any_model_call(conn: sqlite3.Connection, tmp_path: Path) -> None:
    [inv] = store.create_invites(conn, 1, cap_usd_day=0.45, at=T0)
    store.claim_invite(conn, inv.code, consent=True, at=T0)
    (tmp_path / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\npilot = 15.0\n")
    ledger = Ledger(tmp_path / "ledger.jsonl", tmp_path / "budgets.toml")
    today = datetime.now(UTC).isoformat(timespec="seconds")
    ledger.append({"ts": today, "lane": "pilot", "household": inv.household_id, "usd": 0.30})
    assert assert_household_under_cap(conn, ledger, inv.household_id, estimate_usd=0.10) == 0.45
    ledger.append({"ts": today, "lane": "pilot", "household": inv.household_id, "usd": 0.15})
    with pytest.raises(BudgetExceeded):
        assert_household_under_cap(conn, ledger, inv.household_id)
    # Yesterday's spend does not count against today.
    old = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    other = store.create_invites(conn, 1, at=T0)[0]
    ledger.append({"ts": old, "lane": "pilot", "household": other.household_id, "usd": 5.0})
    assert assert_household_under_cap(conn, ledger, other.household_id) == 0.45
    # And inside calls/: the client refuses before its backend is touched.
    client = Client("live", load_config(), NoNetwork(), ledger=ledger, fixtures_root=tmp_path, private=tmp_path)
    import asyncio
    with pytest.raises(BudgetExceeded):
        asyncio.run(client.respond("guide", "hello", lane="pilot", household=inv.household_id,
                                   household_cap_usd=0.45))
    assert NoNetwork.calls == 0
    assert json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])["usd"] == 5.0


def test_snooze_route(api: TestClient, conn: sqlite3.Connection, deps: Deps) -> None:
    a, ha = join(api, conn)
    b, hb = join(api, conn)
    aa = add_appliance(conn, a)
    api.put(f"/api/v1/appliances/{aa}/jobs/{JOB}/last-done", json={"source": "never"}, headers=ha)
    api.post("/api/v1/push/subscriptions", json=Browser().sub, headers=ha)
    [sent] = push.dispatch(conn, T0, deps.load_card, ok)
    url = f"/api/v1/reminders/{sent.key}/snooze"
    assert api.post(url, headers=ha).status_code == 204                      # no body: 24 hours
    assert api.post(url, json={"hours": 2}, headers=ha).status_code == 204
    assert api.post(url, headers=hb).status_code == 404                      # not b's reminder
    assert api.post(url).status_code == 401
    for bad in ({"hours": 0}, {"hours": 73}, {"days": 1}):
        assert api.post(url, json=bad, headers=ha).status_code == 422
    hours = [r["snooze_until"] for r in conn.execute(
        "SELECT snooze_until FROM pilot_events WHERE kind = 'reminder_snoozed' ORDER BY id")]
    assert len(hours) == 2
