"""An upkeep job whose manual gives no interval (contracts 1.20): it is on /due only once a person in the app sets the
household's own schedule, and then reads as theirs, with no manual line, no quote and no page. No agent or Home
Assistant token may set one, a fault fix has no schedule, and its changes and undo work while it is off /due."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from icalendar import Calendar
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.feeds.errors import install_errors
from app.feeds.router import Deps, make_router
from app.schedule.due import due
from app.schedule.remind import reminder, schedule_line, why_line
from contracts.models.card import Card
from integrations.mcp_server.server import _item, _say
from tests.l5.conftest import CARD_PATH, ROOT, T0, acme_card, add_appliance
from tests.l5.test_ha_integration import core
from tests.l5.test_manage import token
from tests.l5.test_pilot_door import join

OWN, FIX = "bleed_radiators", "reset_after_fault"
SPEC = yaml.safe_load((ROOT / "contracts/api.openapi.yaml").read_text())
DUE = Draft202012Validator({"$ref": "urn:api#/components/schemas/Due"},
                           registry=Registry().with_resource("urn:api", Resource.opaque(SPEC)))


def card_with_no_interval_job() -> Card:
    card = acme_card()
    [job] = card.jobs
    own = job.model_copy(update={"id": OWN, "interval": None, "duration_min": None,
                                 "title": job.title.model_copy(update={"root": {"en": "Bleed the radiators",
                                                                               "nl": "Radiatoren ontluchten"}})})
    fix = job.model_copy(update={"id": FIX, "kind": "fix", "interval": None})
    return card.model_copy(update={"jobs": [job, own, fix]})


@pytest.fixture
def loader() -> Callable[[str], Card | None]:
    return {CARD_PATH: card_with_no_interval_job()}.get


@pytest.fixture
def api(conn: sqlite3.Connection, secret: bytes, loader: Callable[[str], Card | None]) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(make_router(Deps(conn, secret, loader, clock=lambda: T0)), prefix="/api/v1")
    install_errors(app)
    with TestClient(app, base_url="https://fixit.test") as c:
        yield c


@pytest.fixture
def home(api: TestClient, conn: sqlite3.Connection) -> tuple[str, str, dict[str, str]]:
    hid, auth = join(api, conn)
    aid = add_appliance(conn, hid)
    return hid, f"/api/v1/appliances/{aid}/jobs", auth


def jobs_on_due(api: TestClient, auth: dict[str, str]) -> list[str]:
    return [d["job_id"] for d in api.get("/api/v1/due", headers=auth).json()]


def test_on_due_only_once_a_person_sets_it_and_then_it_reads_as_theirs(
        api: TestClient, home: tuple[str, str, dict[str, str]]) -> None:
    _, jobs, auth = home
    assert jobs_on_due(api, auth) == ["clean_debris_filter"]  # nothing proposes an interval the manual does not give
    assert api.post(f"{jobs}/{OWN}/done", headers=auth).status_code == 404  # not on /due yet
    r = api.put(f"{jobs}/{OWN}/schedule", json={"kind": "every", "every": 1, "unit": "year"}, headers=auth)
    assert r.status_code == 200, r.text
    d = r.json()
    DUE.validate(d)
    assert (d["manual"], d["interval_quote"], d["quote"], d["page"]) == (None, None, None, None)
    assert d["schedule"] == {"kind": "every", "every": 1, "unit": "year"} and d["status"] == "none"
    assert api.post(f"{jobs}/{OWN}/done", json={"date": "2025-09-20"}, headers=auth).json()["due_on"] == "2026-09-20"
    assert set(jobs_on_due(api, auth)) == {"clean_debris_filter", OWN}
    for item in api.get("/api/v1/due", headers=auth).json():
        DUE.validate(item)  # both shapes: the manual's and the household's own


def test_no_agent_or_home_assistant_token_sets_it(api: TestClient, home: tuple[str, str, dict[str, str]]) -> None:
    _, jobs, auth = home
    for kind in ("agent", "ha"):
        other = token(api, auth, kind, "write")
        for body in ({"kind": "every", "every": 1, "unit": "year"}, {"kind": "manual"}):
            r = api.put(f"{jobs}/{OWN}/schedule", json=body, headers=other)
            assert r.status_code == 403 and r.json()["error"] == "person_only", (kind, body)
    assert OWN not in jobs_on_due(api, auth)
    api.put(f"{jobs}/{OWN}/schedule", json={"kind": "every", "every": 1, "unit": "year"}, headers=auth)
    agent = token(api, auth, "agent", "write")
    assert api.post(f"{jobs}/{OWN}/done", headers=agent).status_code == 200  # once set, any writer marks it done
    r = api.put(f"{jobs}/{OWN}/schedule", json={"kind": "every", "every": 2, "unit": "year"}, headers=agent)
    assert r.status_code == 403  # and still cannot change it
    read = token(api, auth, "agent", "read")
    assert api.put(f"{jobs}/clean_debris_filter/schedule", json={"kind": "manual"}, headers=read).status_code == 403


def test_a_fault_fix_has_no_schedule(api: TestClient, home: tuple[str, str, dict[str, str]]) -> None:
    _, jobs, auth = home
    r = api.put(f"{jobs}/{FIX}/schedule", json={"kind": "every", "every": 1, "unit": "year"}, headers=auth)
    assert r.status_code == 422 and r.json()["error"] == "fix_has_no_schedule"
    assert api.get(f"{jobs}/{FIX}/changes", headers=auth).status_code == 404
    assert api.put(f"{jobs}/nope/schedule", json={"kind": "manual"}, headers=auth).status_code == 404


def test_manual_takes_it_off_due_and_undo_brings_it_back(api: TestClient,
                                                         home: tuple[str, str, dict[str, str]]) -> None:
    _, jobs, auth = home
    api.put(f"{jobs}/{OWN}/schedule", json={"kind": "window", "min": 10, "max": 14, "unit": "month"}, headers=auth)
    r = api.put(f"{jobs}/{OWN}/schedule", json={"kind": "manual"}, headers=auth)
    assert r.status_code == 204 and not r.content and OWN not in jobs_on_due(api, auth)
    changes = api.get(f"{jobs}/{OWN}/changes", headers=auth)
    assert changes.status_code == 200 and [c["kind"] for c in changes.json()] == ["schedule", "schedule"]
    back = api.post(f"{jobs}/{OWN}/undo", headers=auth)  # undoes {kind: manual}
    assert back.status_code == 200 and back.json()["schedule"]["kind"] == "window"
    gone = api.post(f"{jobs}/{OWN}/undo", headers=auth)  # undoes the first schedule: nothing left
    assert gone.status_code == 204 and OWN not in jobs_on_due(api, auth)
    assert api.post(f"{jobs}/{OWN}/undo", headers=auth).status_code == 409  # nothing to undo


def test_the_words_say_it_is_theirs_and_cite_no_page(api: TestClient, conn: sqlite3.Connection,
                                                     home: tuple[str, str, dict[str, str]],
                                                     loader: Callable[[str], Card | None]) -> None:
    hid, jobs, auth = home
    api.put(f"{jobs}/{OWN}/schedule", json={"kind": "every", "every": 1, "unit": "year"}, headers=auth)
    api.post(f"{jobs}/{OWN}/done", json={"date": "2025-09-20"}, headers=auth)
    [d] = [d for d in due(conn, hid, T0, loader) if d.job_id == OWN]
    r = reminder(d, T0.date())
    assert r.footnote == "your schedule · the manual gives no interval" and r.page is None
    assert why_line(d) == "Last done: 20 Sep. You chose every year. The manual gives no interval."
    assert schedule_line(d) == f"Washer: {d.due_name} due · you chose every year · last done: 20 Sep."
    assert "manual, p" not in r.payload()["body"]
    nl = d.__class__(**{**d.__dict__, "lang": "nl"})
    assert why_line(nl) == "Laatst gedaan: 20 sep. Jij koos elk jaar. De handleiding geeft geen interval."
    url = api.get("/api/v1/household/feeds", headers=auth).json()["ics_url"]
    [ev] = [e for e in Calendar.from_ical(api.get(httpx.URL(url).path).content).walk("VEVENT")
            if str(e["url"]).endswith(f"/{OWN}")]
    desc = str(ev["description"])
    assert "The manual gives no interval." in desc and "“" not in desc and "manual, p" not in desc


def test_an_agent_and_home_assistant_say_whose_it_is(api: TestClient,
                                                     home: tuple[str, str, dict[str, str]]) -> None:
    _, jobs, auth = home
    d: dict[str, Any] = api.put(f"{jobs}/{OWN}/schedule", json={"kind": "every", "every": 1, "unit": "year"},
                                headers=auth).json()
    assert "manual" not in _item(d) and "household's own choice" in _item(d)["note"]
    assert "note" not in _item(api.get("/api/v1/due", headers=auth).json()[0])
    refused = api.put(f"{jobs}/{OWN}/schedule", json={"kind": "manual"}, headers=token(api, auth, "agent", "write"))
    with pytest.raises(Exception, match="only a person in the app"):
        _say(httpx.Response(refused.status_code, json=refused.json()))
    [job] = [j for j in core.parse_due([d], {}) if j.job_id == OWN]
    assert job.interval_quote is None and job.attributes()["interval"] is None and job.page is None
    assert job.description() == "you chose every year · the manual gives no interval · last done: not known"
