"""The .ics feed follows the household's own schedule: a window spans its days, a snooze moves the entry, each entry
links to the job in the app, and nothing in the calendar can change the schedule or carries a token."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from icalendar import Calendar

from tests.l5.test_manage import home  # noqa: F401 (fixture)
from tests.l5.test_pilot_door import JOB, api, clock, deps  # noqa: F401 (fixtures)


def calendar(api: TestClient, auth: dict[str, str]) -> Calendar:  # noqa: F811
    url = api.get("/api/v1/household/feeds", headers=auth).json()["ics_url"]
    r = api.get(urlsplit(url).path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    return Calendar.from_ical(r.content)


def test_a_window_spans_its_days_in_the_event_and_the_todo(api: TestClient,  # noqa: F811
                                                           home: tuple[str, dict[str, str]]) -> None:  # noqa: F811
    job, auth = home
    api.post(f"{job}/done", json={"date": "2026-06-27"}, headers=auth)
    api.put(f"{job}/schedule", json={"kind": "window", "min": 3, "max": 6, "unit": "month"}, headers=auth)
    cal = calendar(api, auth)
    [ev], [todo] = cal.walk("VEVENT"), cal.walk("VTODO")
    assert ev.decoded("dtstart") == date(2026, 9, 27) and ev.decoded("dtend") == date(2026, 12, 28)  # to 27 Dec
    assert todo.decoded("dtstart") == date(2026, 9, 27) and todo.decoded("due") == date(2026, 12, 27)
    assert str(ev["description"]).startswith("Any day from 27 Sep to 27 Dec.\n")
    assert len(ev.walk("VALARM")) == 1


def test_one_day_without_a_window(api: TestClient, home: tuple[str, dict[str, str]]) -> None:  # noqa: F811
    job, auth = home
    api.post(f"{job}/done", json={"date": "2026-10-01"}, headers=auth)
    [ev], [todo] = (cal := calendar(api, auth)).walk("VEVENT"), cal.walk("VTODO")
    assert (ev.decoded("dtstart"), ev.decoded("dtend")) == (date(2027, 1, 1), date(2027, 1, 2))
    assert "dtstart" not in todo and todo.decoded("due") == date(2027, 1, 1)
    assert not str(ev["description"]).startswith("Any day")


def test_a_snooze_moves_the_entry_later(api: TestClient, home: tuple[str, dict[str, str]]) -> None:  # noqa: F811
    job, auth = home
    api.post(f"{job}/done", json={"date": "2026-07-01"}, headers=auth)  # due 1 Oct: overdue
    assert api.put(f"{job}/snooze", json={"until": "2026-11-03"}, headers=auth).status_code == 200
    [ev] = calendar(api, auth).walk("VEVENT")
    assert ev.decoded("dtstart") == date(2026, 11, 3)


def test_each_entry_links_to_the_job_and_the_feed_holds_no_token(api: TestClient,  # noqa: F811
                                                                 home: tuple[str, dict[str, str]]) -> None:  # noqa: F811
    job, auth = home
    api.post(f"{job}/done", json={"date": "2026-10-01"}, headers=auth)
    aid = job.split("/")[4]
    raw = api.get(urlsplit(api.get("/api/v1/household/feeds", headers=auth).json()["ics_url"]).path).content
    [ev] = Calendar.from_ical(raw).walk("VEVENT")
    url = str(ev["url"])
    assert url.endswith(f"&ch=ics#job/{aid}/{JOB}") and url.startswith("https://fixit.test/?")
    assert f"Mark it done or snooze it: https://fixit.test/#job/{aid}/{JOB}" in str(ev["description"])
    token = auth["Authorization"].removeprefix("Bearer ")
    assert token.encode() not in raw and b"Bearer" not in raw
    before = api.get("/api/v1/due", headers=auth).json()
    assert api.get(urlsplit(url).path + "?" + urlsplit(url).query).status_code in (200, 404)  # following it is a GET
    assert api.get("/api/v1/due", headers=auth).json() == before
