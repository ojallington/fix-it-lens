"""Each person's alerts decide who a reminder reaches: an assigned job its assignee only, an unassigned one whoever
follows its item, push off reaches no one, and someone in quiet hours gets it when they end. A person's own calendar
feed carries what they follow."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.feeds import push
from app.feeds.errors import install_errors
from app.feeds.router import Deps, make_router
from app.schedule import store
from app.schedule.remind import Reminder
from contracts.models.card import Card
from tests.l5.conftest import T0, add_appliance
from tests.l5.test_manage import token
from tests.l5.test_people import DEFAULTS, joined
from tests.l5.test_pilot_door import Clock, join

JOB = "clean_debris_filter"


@pytest.fixture
def clock() -> Clock:
    return Clock(T0)  # 13:00 in Amsterdam


@pytest.fixture
def api(conn: sqlite3.Connection, secret: bytes, loader: Callable[[str], Card | None],
        clock: Clock) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(make_router(Deps(conn, secret, loader, None, clock, invite_failures_per_hour=50)),
                       prefix="/api/v1")
    install_errors(app)
    with TestClient(app, base_url="https://fixit.test") as c:
        yield c


class Phones:
    """Stands in for the push service: which browser got which reminder."""

    def __init__(self) -> None:
        self.got: list[tuple[str, str]] = []

    def __call__(self, sub: push.Subscription, r: Reminder) -> int:
        self.got.append((sub.endpoint.rsplit("/", 1)[1], r.key))
        return 201

    def take(self) -> list[str]:
        out, self.got = sorted(who for who, _ in self.got), []
        return out


def browser(name: str) -> dict[str, Any]:
    return {"endpoint": f"https://push.example.invalid/send/{name}", "keys": {"p256dh": "k", "auth": "a"}}


def subscribe(api: TestClient, auth: dict[str, str], name: str) -> None:
    assert api.post("/api/v1/push/subscriptions", json=browser(name), headers=auth).status_code == 201


def home(api: TestClient, conn: sqlite3.Connection) -> tuple[str, str, dict[str, dict[str, str]], dict[str, str]]:
    """A home with a due job and three people, each with one browser: Admin, Sam (member) and Kim (view-only)."""
    hid, admin = join(api, conn)
    aid = add_appliance(conn, hid)
    store.set_last_done(conn, hid, aid, JOB, source="never", on=None, at=T0)
    sam, sam_auth = joined(api, admin, "Sam")
    kim, kim_auth = joined(api, admin, "Kim", view_only=True)
    auth = {"admin": admin, "sam": sam_auth, "kim": kim_auth}
    for name, a in auth.items():
        subscribe(api, a, name)
    return hid, aid, auth, {"sam": sam, "kim": kim}


def alerts(api: TestClient, auth: dict[str, str], **change: Any) -> None:
    r = api.put("/api/v1/me/alerts", json={**DEFAULTS, **change}, headers=auth)
    assert r.status_code == 200, r.text


def sent_rows(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM pilot_events WHERE kind = 'reminder_sent'").fetchone()[0])


def test_an_assigned_job_reaches_its_assignee_only(api: TestClient, conn: sqlite3.Connection,
                                                    loader: Callable[[str], Card | None]) -> None:
    _, aid, auth, ids = home(api, conn)
    url = f"/api/v1/appliances/{aid}/jobs/{JOB}/assignee"
    assert api.put(url, json={"person_id": ids["kim"]}, headers=auth["admin"]).status_code == 200
    phones = Phones()
    [s] = push.dispatch(conn, T0, loader, phones)
    assert phones.take() == ["kim"] and s.people == ("Kim",)  # a viewer is reminded of what is theirs
    assert push.dispatch(conn, T0 + timedelta(minutes=5), loader, phones) == [] and phones.take() == []
    assert sent_rows(conn) == 1


def test_an_unassigned_job_reaches_those_who_follow_it(api: TestClient, conn: sqlite3.Connection,
                                                        loader: Callable[[str], Card | None]) -> None:
    _, aid, auth, _ = home(api, conn)
    alerts(api, auth["sam"], push=False)
    alerts(api, auth["kim"], items="chosen", follows=[])
    phones = Phones()
    push.dispatch(conn, T0, loader, phones)
    assert phones.take() == ["admin"]
    alerts(api, auth["kim"], items="chosen", follows=[aid])  # Kim follows the washer now
    push.dispatch(conn, T0 + timedelta(minutes=5), loader, phones)
    assert phones.take() == ["kim"]  # and the admin is not pushed twice
    assert sent_rows(conn) == 1  # the home's count is one reminder, however many people it reached


def test_quiet_hours_wait_for_their_end(api: TestClient, conn: sqlite3.Connection,
                                        loader: Callable[[str], Card | None]) -> None:
    _, _, auth, _ = home(api, conn)
    alerts(api, auth["sam"], quiet={"from": "12:30", "to": "14:00"})   # 13:00 there now
    alerts(api, auth["kim"], quiet={"from": "22:00", "to": "13:30"})   # over midnight, till 13:30
    phones = Phones()
    [s] = push.dispatch(conn, T0, loader, phones)
    assert phones.take() == ["admin"] and sorted(s.waiting) == ["Kim", "Sam"]
    push.dispatch(conn, T0 + timedelta(minutes=45), loader, phones)  # 13:45
    assert phones.take() == ["kim"]
    push.dispatch(conn, T0 + timedelta(hours=1), loader, phones)  # 14:00
    assert phones.take() == ["sam"]
    assert push.dispatch(conn, T0 + timedelta(hours=2), loader, phones) == []
    assert sent_rows(conn) == 1


def test_a_snooze_reaches_each_person_once_more(api: TestClient, conn: sqlite3.Connection,
                                                loader: Callable[[str], Card | None]) -> None:
    hid, _, auth, _ = home(api, conn)
    phones = Phones()
    [s] = push.dispatch(conn, T0, loader, phones)
    assert phones.take() == ["admin", "kim", "sam"]
    assert store.record_reminder_snoozed(conn, hid, s.key, hours=3, at=T0 + timedelta(minutes=10))
    push.dispatch(conn, T0 + timedelta(hours=2), loader, phones)
    assert phones.take() == []  # not before the snooze runs out
    alerts(api, auth["sam"], quiet={"from": "15:00", "to": "16:00"})
    push.dispatch(conn, T0 + timedelta(hours=3, minutes=10), loader, phones)  # 16:10: none of them quiet
    push.dispatch(conn, T0 + timedelta(hours=3, minutes=15), loader, phones)
    assert phones.take() == ["admin", "kim", "sam"]  # once more each, never a date moved
    assert sent_rows(conn) == 2


def test_an_older_subscription_is_the_first_admins(api: TestClient, conn: sqlite3.Connection,
                                                   loader: Callable[[str], Card | None]) -> None:
    hid, auth = join(api, conn)
    aid = add_appliance(conn, hid)
    store.set_last_done(conn, hid, aid, JOB, source="never", on=None, at=T0)
    push.subscribe(conn, hid, browser("old"), at=T0)  # from before people had their own alerts: no person
    alerts(api, auth, push=False)
    phones = Phones()
    assert push.dispatch(conn, T0, loader, phones) == [] and phones.take() == []
    alerts(api, auth, push=True)
    push.dispatch(conn, T0, loader, phones)
    assert phones.take() == ["old"]


def test_anyone_in_the_home_subscribes_their_own_browser(api: TestClient, conn: sqlite3.Connection) -> None:
    _, auth, = join(api, conn)
    kim, viewer = joined(api, auth, "Kim", view_only=True)
    subscribe(api, viewer, "kim")
    assert conn.execute("SELECT person_id FROM push_subscriptions").fetchone()[0] == kim
    for kind in ("agent", "ha"):
        r = api.post("/api/v1/push/subscriptions", json=browser(kind), headers=token(api, auth, kind, "write"))
        assert r.status_code == 403
    r = api.request("DELETE", "/api/v1/push/subscriptions", json={"endpoint": browser("kim")["endpoint"]},
                    headers=viewer)
    assert r.status_code == 204 and conn.execute("SELECT count(*) FROM push_subscriptions").fetchone()[0] == 0


def test_the_operators_send_keeps_each_persons_alerts(api: TestClient, conn: sqlite3.Connection,
                                                      loader: Callable[[str], Card | None]) -> None:
    hid, _, auth, _ = home(api, conn)
    alerts(api, auth["sam"], push=False)
    alerts(api, auth["kim"], quiet={"from": "12:00", "to": "14:00"})
    phones = Phones()
    _, s = push.send_now(conn, hid, JOB, T0, loader, phones)
    assert phones.take() == ["admin"] and s.waiting == ("Kim",)
    _, s = push.send_now(conn, hid, JOB, T0, loader, phones)  # a second look still sends
    assert phones.take() == ["admin"]
    push.dispatch(conn, T0 + timedelta(hours=1), loader, phones)
    assert phones.take() == ["kim"]  # the admin had it from the operator; Kim's quiet hours have ended


def events(ics: bytes) -> int:
    return ics.count(b"BEGIN:VEVENT")


def test_each_person_has_their_own_calendar(api: TestClient, conn: sqlite3.Connection) -> None:
    _, aid, auth, ids = home(api, conn)
    url = {who: api.get("/api/v1/household/feeds", headers=a).json()["ics_url"] for who, a in auth.items()}
    assert len(set(url.values())) == 3
    path = {who: u.split("fixit.test")[1] for who, u in url.items()}
    for who in path:
        assert events(api.get(path[who]).content) == 1
    alerts(api, auth["kim"], items="chosen", follows=[])
    alerts(api, auth["sam"], calendar=False)
    assert events(api.get(path["kim"]).content) == 0 and events(api.get(path["sam"]).content) == 0
    assert api.get(path["sam"]).status_code == 200  # still a calendar, so a subscribed app keeps it, empty
    assignee = f"/api/v1/appliances/{aid}/jobs/{JOB}/assignee"
    api.put(assignee, json={"person_id": ids["kim"]}, headers=auth["admin"])
    assert events(api.get(path["kim"]).content) == 1 and events(api.get(path["admin"]).content) == 0
    whole = api.get("/api/v1/household/feeds", headers=token(api, auth["admin"], "ha", "read")).json()["ics_url"]
    assert events(api.get(whole.split("fixit.test")[1]).content) == 1  # Home Assistant sees the whole home
    assert api.delete(f"/api/v1/household/people/{ids['kim']}", headers=auth["admin"]).status_code == 204
    assert api.get(path["kim"]).status_code == 404  # someone who left has no feed
    forged = path["sam"].replace(ids["sam"], ids["kim"])
    assert api.get(forged).status_code == 404
