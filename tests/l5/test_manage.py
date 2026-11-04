"""The household's own schedule over REST: mark done, snooze (only ever later), its own interval or window beside the
manual's, undo, the change log with who made each change, and the tokens it issues (read-only and write) to its
agents and its Home Assistant (app/schedule/manage.py, app/feeds/router.py)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.feeds.errors import install_errors
from app.feeds.router import Deps, make_router
from tests.l5.conftest import T0, add_appliance
from tests.l5.test_pilot_door import JOB, Clock, api, clock, deps, join  # noqa: F401 (fixtures)

TODAY = T0.date()  # 13:00 in Amsterdam


@pytest.fixture
def home(api: TestClient, conn: sqlite3.Connection) -> tuple[str, dict[str, str]]:  # noqa: F811
    hid, auth = join(api, conn)
    aid = add_appliance(conn, hid)
    return f"/api/v1/appliances/{aid}/jobs/{JOB}", auth


def token(api: TestClient, auth: dict[str, str], by_kind: str, scope: str) -> dict[str, str]:  # noqa: F811
    r = api.post("/api/v1/household/tokens", json={"by_kind": by_kind, "scope": scope, "label": "test"}, headers=auth)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_done_and_the_manuals_interval(api: TestClient, home: tuple[str, dict[str, str]]) -> None:  # noqa: F811
    job, auth = home
    d = api.post(f"{job}/done", json={"date": "2026-10-01"}, headers=auth).json()
    assert d["due_on"] == "2027-01-01" and d["status"] == "upcoming" and d["last_done"]["source"] == "user"
    assert d["manual"] == {"every": 3, "unit": "month", "page": d["page"], "quote": d["quote"]}
    assert d["schedule"] is None and d["window_end"] is None and d["snoozed_until"] is None
    assert api.post(f"{job}/done", json={"date": "2026-10-28"}, headers=auth).status_code == 422  # not ahead
    assert api.post(f"{job}/done", headers=auth).json()["last_done"]["date"] == TODAY.isoformat()  # no body: today


def test_a_window_is_due_from_min_and_overdue_after_max(api: TestClient, home: tuple[str, dict[str, str]],  # noqa: F811
                                                        clock: Clock) -> None:  # noqa: F811
    job, auth = home
    api.post(f"{job}/done", json={"date": "2026-06-27"}, headers=auth)
    d = api.put(f"{job}/schedule", json={"kind": "window", "min": 3, "max": 6, "unit": "month"}, headers=auth).json()
    assert d["schedule"] == {"kind": "window", "min": 3, "max": 6, "unit": "month"}
    assert (d["due_on"], d["window_end"], d["status"]) == ("2026-09-27", "2026-12-27", "due")
    assert d["manual"]["every"] == 3  # the manual's value stays beside "yours"
    clock.t = T0 + timedelta(days=62)  # 28 Dec
    assert api.get("/api/v1/due", headers=auth).json()[0]["status"] == "overdue"
    d = api.put(f"{job}/schedule", json={"kind": "every", "every": 4, "unit": "month"}, headers=auth).json()
    assert (d["due_on"], d["window_end"]) == ("2026-10-27", None)
    d = api.put(f"{job}/schedule", json={"kind": "manual"}, headers=auth).json()
    assert d["schedule"] is None and d["due_on"] == "2026-09-27"
    for bad in ({"kind": "window", "min": 6, "max": 3, "unit": "month"}, {"kind": "every", "unit": "month"},
                {"kind": "every", "every": 2, "unit": "cycle"},
                {"kind": "every", "every": 2, "unit": "month", "x": 1}, {"kind": "manual", "every": 2}):
        assert api.put(f"{job}/schedule", json=bad, headers=auth).status_code == 422, bad


def test_a_snooze_only_moves_it_later(api: TestClient, home: tuple[str, dict[str, str]]) -> None:  # noqa: F811
    job, auth = home
    api.post(f"{job}/done", json={"date": "2026-07-01"}, headers=auth)  # due 1 Oct: overdue
    for early in ("2026-10-27", "2026-09-30"):
        r = api.put(f"{job}/snooze", json={"until": early}, headers=auth)
        assert r.status_code == 422 and "only moves it later" in r.json()["detail"]
    d = api.put(f"{job}/snooze", json={"until": "2026-11-03"}, headers=auth).json()
    assert (d["due_on"], d["snoozed_until"], d["status"]) == ("2026-11-03", "2026-11-03", "upcoming")
    assert api.put(f"{job}/snooze", json={"until": "2026-11-01"}, headers=auth).status_code == 422  # not back
    d = api.post(f"{job}/done", headers=auth).json()  # done clears the snooze
    assert (d["snoozed_until"], d["due_on"]) == (None, "2027-01-27")


def test_undo_walks_back_one_change_at_a_time(api: TestClient, home: tuple[str, dict[str, str]],  # noqa: F811
                                              conn: sqlite3.Connection) -> None:
    job, auth = home
    assert api.post(f"{job}/undo", headers=auth).status_code == 409  # nothing to undo
    api.post(f"{job}/done", json={"date": "2026-09-01"}, headers=auth)
    api.put(f"{job}/snooze", json={"until": "2026-12-15"}, headers=auth)
    ch = api.get(f"{job}/changes", headers=auth).json()
    admin = conn.execute("SELECT id FROM people").fetchone()["id"]  # the household token speaks for its first admin
    by = {"kind": "app", "person_id": admin, "label": "Admin"}
    assert [(c["kind"], c["undone"], c["by"]) for c in ch] == [("snooze", False, by), ("done", False, by)]
    assert ch[0]["after"]["snoozed_until"] == "2026-12-15" and "_history_id" not in ch[1]["after"]
    d = api.post(f"{job}/undo", headers=auth).json()
    assert (d["snoozed_until"], d["due_on"]) == (None, "2026-12-01")
    d = api.post(f"{job}/undo", headers=auth).json()
    assert d["reason"] == "unanswered" and d["due_on"] is None  # back to before anything was said
    ch = api.get(f"{job}/changes", headers=auth).json()
    assert [(c["kind"], c["undone"]) for c in ch] == [("undo", False), ("undo", False), ("snooze", True),
                                                      ("done", True)]
    assert api.post(f"{job}/undo", headers=auth).status_code == 409  # an undo is not undoable


def test_own_undo_takes_back_only_this_keys_change(deps: Deps, conn: sqlite3.Connection) -> None:  # noqa: F811
    """{"own": true} matches the change's token id, not its label: two keys both labelled "test" are two callers."""
    app = FastAPI()
    app.include_router(make_router(deps), prefix="/api/v1")
    install_errors(app)  # the contract's Error body, as the server has it
    client = TestClient(app, base_url="https://fixit.test")
    hid, auth = join(client, conn)
    job = f"/api/v1/appliances/{add_appliance(conn, hid)}/jobs/{JOB}"
    agent, other = token(client, auth, "agent", "write"), token(client, auth, "agent", "write")
    client.post(f"{job}/done", json={"date": "2026-09-01"}, headers=agent)
    for caller in (other, auth):  # the same label on another key, and the household's own token
        r = client.post(f"{job}/undo", json={"own": True}, headers=caller)
        assert r.status_code == 409 and r.json()["error"] == "not_own", r.text
    assert client.get(f"{job}/changes", headers=auth).json()[0]["undone"] is False  # nothing was undone
    d = client.post(f"{job}/undo", json={"own": True}, headers=agent).json()
    assert d["reason"] == "unanswered" and d["due_on"] is None  # back to before the agent said anything
    client.post(f"{job}/done", json={"date": "2026-09-01"}, headers=auth)  # now a person changed it last
    assert client.post(f"{job}/undo", json={"own": True}, headers=agent).json()["error"] == "not_own"
    assert client.post(f"{job}/undo", json={"own": False}, headers=agent).status_code == 200  # as before: the latest
    assert client.post(f"{job}/undo", json={"own": "yes", "x": 1}, headers=agent).status_code == 422


def test_undo_refuses_when_the_job_changed_since(api: TestClient, home: tuple[str, dict[str, str]],  # noqa: F811
                                                 conn: sqlite3.Connection) -> None:
    job, auth = home
    api.post(f"{job}/done", json={"date": "2026-09-01"}, headers=auth)
    conn.execute("UPDATE job_state SET last_done = '2026-10-20', last_done_source = 'checked'")  # a checked session
    assert api.post(f"{job}/undo", headers=auth).status_code == 409
    assert api.get("/api/v1/due", headers=auth).json()[0]["last_done"] == {"date": "2026-10-20", "source": "checked"}


def test_the_last_done_answer_is_a_change_too(api: TestClient, home: tuple[str, dict[str, str]]) -> None:  # noqa: F811
    job, auth = home
    d = api.put(f"{job}/last-done", json={"source": "never"}, headers=auth).json()
    assert d["reason"] == "never" and d["status"] == "due"
    assert [c["kind"] for c in api.get(f"{job}/changes", headers=auth).json()] == ["last_done"]
    assert api.post(f"{job}/undo", headers=auth).json()["reason"] == "unanswered"


def test_who_made_it_comes_from_the_token(api: TestClient, home: tuple[str, dict[str, str]]) -> None:  # noqa: F811
    job, auth = home
    ha = token(api, auth, "ha", "write")
    agent_read = token(api, auth, "agent", "read")
    assert api.post(f"{job}/done", headers=ha).status_code == 200
    assert api.get("/api/v1/due", headers=agent_read).status_code == 200  # a read token reads
    for method, path, body in (("post", f"{job}/done", None), ("put", f"{job}/snooze", {"until": "2027-03-01"}),
                               ("post", f"{job}/undo", None), ("put", f"{job}/last-done", {"source": "never"})):
        assert api.request(method, path, json=body, headers=agent_read).status_code == 403, path
    # only the household's own app manages tokens and deletes the household
    assert api.post("/api/v1/household/tokens", json={"by_kind": "agent", "scope": "read", "label": "x"},
                    headers=ha).status_code == 403
    for scope in ("read", "write"):  # a calendar address comes from /household/feeds, never from here
        r = api.post("/api/v1/household/tokens", json={"by_kind": "feed", "scope": scope, "label": "cal"},
                     headers=auth)
        assert r.status_code == 422 and "/household/feeds" in r.text, r.text
    assert api.delete("/api/v1/household", headers=ha).status_code == 403
    toks = api.get("/api/v1/household/tokens", headers=auth).json()
    assert [(t["by_kind"], t["label"]) for t in toks] == [("ha", "test"), ("agent", "test")]
    assert toks[0]["last_used_at"] is not None and toks[1]["last_used_at"] is not None
    assert api.get(f"{job}/changes", headers=ha).json()[0]["by"] == {"kind": "ha", "person_id": None, "label": "test"}


def test_a_revoked_token_and_another_household_get_nothing(api: TestClient, conn: sqlite3.Connection,  # noqa: F811
                                                           home: tuple[str, dict[str, str]]) -> None:
    job, auth = home
    r = api.post("/api/v1/household/tokens", json={"by_kind": "agent", "scope": "write", "label": "my agent"},
                 headers=auth).json()
    agent = {"Authorization": f"Bearer {r['token']}"}
    assert "token" not in api.get("/api/v1/household/tokens", headers=auth).json()[0]  # shown once
    assert api.delete(f"/api/v1/household/tokens/{r['id']}", headers=auth).status_code == 204
    assert api.get("/api/v1/due", headers=agent).status_code == 401
    forged = {"Authorization": f"Bearer fxt_{'0' * 43}"}
    assert api.get("/api/v1/due", headers=forged).status_code == 401
    _, other = join(api, conn)
    assert api.post(f"{job}/done", headers=other).status_code == 404  # not their job
    assert api.delete(f"/api/v1/household/tokens/{r['id']}", headers=other).status_code == 404


def test_a_snoozed_job_is_pushed_on_its_new_date(conn: sqlite3.Connection, api: TestClient,  # noqa: F811
                                                 home: tuple[str, dict[str, str]], clock: Clock) -> None:  # noqa: F811
    from app.schedule.due import due
    from tests.l5.conftest import acme_card

    job, auth = home
    api.put(f"{job}/last-done", json={"source": "never"}, headers=auth)
    api.put(f"{job}/snooze", json={"until": "2026-11-02"}, headers=auth)
    hid = conn.execute("SELECT id FROM households").fetchone()["id"]
    load = {"cards/acme/WM-0000.yaml": acme_card()}.get
    [d] = due(conn, hid, T0, load)
    assert not d.is_due(T0) and d.due_on == date(2026, 11, 2)
    assert d.is_due(T0 + timedelta(days=6))  # 2 Nov, after 09:00 there


def test_the_reminder_says_whose_schedule_it_is(api: TestClient, home: tuple[str, dict[str, str]],  # noqa: F811
                                                conn: sqlite3.Connection) -> None:
    from app.schedule.due import due
    from app.schedule.remind import why_line
    from tests.l5.conftest import acme_card

    job, auth = home
    api.post(f"{job}/done", json={"date": "2026-06-27"}, headers=auth)
    hid = conn.execute("SELECT id FROM households").fetchone()["id"]
    load = {"cards/acme/WM-0000.yaml": acme_card()}.get
    page = due(conn, hid, T0, load)[0].page
    assert why_line(due(conn, hid, T0, load)[0]).endswith(f"(p. {page}).")  # the manual's only
    api.put(f"{job}/schedule", json={"kind": "window", "min": 3, "max": 6, "unit": "month"}, headers=auth)
    assert why_line(due(conn, hid, T0, load)[0]).endswith("You chose every 3 to 6 months.")
    api.put(f"{job}/schedule", json={"kind": "every", "every": 1, "unit": "month"}, headers=auth)
    assert why_line(due(conn, hid, T0, load)[0]).endswith("You chose every month.")


def test_a_cycle_job_gets_a_date_once_given_a_time() -> None:
    from app.schedule.due import Schedule, job_due_for
    from tests.l5.conftest import acme_card

    card = acme_card()
    job = next(j for j in card.jobs if j.id == JOB)
    assert job.interval is not None
    cycles = job.model_copy(update={"interval": job.interval.model_copy(update={"every": 30, "unit": "cycle"})})
    last = date(2026, 9, 1)
    assert job_due_for(cycles, card, last_done=last, source="user", today=TODAY) == (None, "cycle", None, "none")
    yours = Schedule(kind="every", unit="month", every=2)
    got = job_due_for(cycles, card, last_done=last, source="user", today=TODAY, schedule=yours)
    assert got == (date(2026, 11, 1), "scheduled", None, "upcoming")
