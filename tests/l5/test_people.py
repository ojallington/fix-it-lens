"""The people in a home: an invite by link, joining with a name, roles (admin, member, view-only member), a home that
keeps one admin, removing someone, and assigning a job. Every body is the contract's."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.feeds.errors import install_errors
from app.feeds.router import Deps, make_router
from contracts.models.card import Card
from tests.l5.conftest import ROOT, T0, add_appliance
from tests.l5.test_manage import token
from tests.l5.test_pilot_door import Clock, join

SPEC = yaml.safe_load((ROOT / "contracts/api.openapi.yaml").read_text())
REG = Registry().with_resource("urn:api", Resource.opaque(SPEC))
PERSON, INVITE, DUE = (Draft202012Validator({"$ref": f"urn:api#/components/schemas/{n}"}, registry=REG)
                       for n in ("Person", "Invite", "Due"))
JOB = "clean_debris_filter"


@pytest.fixture
def clock() -> Clock:
    return Clock(T0)


@pytest.fixture
def api(conn: sqlite3.Connection, secret: bytes, loader: Callable[[str], Card | None],
        clock: Clock) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(make_router(Deps(conn, secret, loader, None, clock, invite_failures_per_hour=50)),
                       prefix="/api/v1")
    install_errors(app)
    with TestClient(app, base_url="https://fixit.test") as c:
        yield c


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def invite(api: TestClient, auth: dict[str, str], **body: Any) -> dict[str, Any]:
    r = api.post("/api/v1/household/invites", json={"role": "member", **body}, headers=auth)
    assert r.status_code == 201, r.text
    INVITE.validate(r.json())
    return r.json()  # type: ignore[no-any-return]


def joined(api: TestClient, auth: dict[str, str], name: str, **body: Any) -> tuple[str, dict[str, str]]:
    code = invite(api, auth, **body)["code"]
    r = api.post("/api/v1/household/join", json={"code": code, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["person"]["id"], bearer(r.json()["token"])


def test_an_invite_link_brings_someone_in_once(api: TestClient, conn: sqlite3.Connection, clock: Clock) -> None:
    hid, auth = join(api, conn)
    inv = invite(api, auth, email="sam@example.org")
    assert inv["join_url"] == f"https://fixit.test/#join/{inv['code']}" and inv["expires_at"] == "2026-11-03T12:00:00Z"
    assert conn.execute("SELECT count(*) FROM people_invites WHERE code_sha256 = ?",
                        (hashlib.sha256(inv["code"].encode()).hexdigest(),)).fetchone()[0] == 1
    r = api.post("/api/v1/household/join", json={"code": inv["code"], "name": "Sam"})
    assert r.status_code == 201, r.text
    PERSON.validate(r.json()["person"])
    assert r.json()["household"]["id"] == hid
    assert r.json()["person"] | {"id": "x"} == {"id": "x", "name": "Sam", "role": "member", "view_only": False,
                                                  "me": True}
    again = api.post("/api/v1/household/join", json={"code": inv["code"], "name": "Kim"})
    assert again.status_code == 403  # used
    late = invite(api, auth, expires_in_days=1)["code"]
    clock.t = T0 + timedelta(days=1)
    assert api.post("/api/v1/household/join", json={"code": late, "name": "Kim"}).text == again.text  # expired
    assert api.post("/api/v1/household/join", json={"code": "nope", "name": "Kim"}).text == again.text  # unknown
    listed = api.get("/api/v1/household/people", headers=bearer(r.json()["token"])).json()
    assert [(p["name"], p["role"], p["me"]) for p in listed] == [("Admin", "admin", False), ("Sam", "member", True)]
    for p in listed:
        PERSON.validate(p)


def test_a_taken_name_leaves_the_invite_unused(api: TestClient, conn: sqlite3.Connection) -> None:
    _, auth = join(api, conn)
    code = invite(api, auth)["code"]
    r = api.post("/api/v1/household/join", json={"code": code, "name": "Admin"})
    assert r.status_code == 409 and r.json()["error"] == "name_taken"
    assert api.post("/api/v1/household/join", json={"code": code, "name": "Sam"}).status_code == 201


def test_only_an_admin_invites_and_changes_roles(api: TestClient, conn: sqlite3.Connection) -> None:
    _, auth = join(api, conn)
    sam, member = joined(api, auth, "Sam")
    _, viewer = joined(api, auth, "Kim", view_only=True)
    for who in (member, viewer, token(api, auth, "agent", "write")):
        assert api.post("/api/v1/household/invites", json={"role": "member"}, headers=who).status_code == 403
    assert api.patch(f"/api/v1/household/people/{sam}", json={"role": "admin"}, headers=member).status_code == 403
    r = api.patch(f"/api/v1/household/people/{sam}", json={"name": "Sammy"}, headers=member)  # their own name
    assert r.status_code == 200 and r.json()["name"] == "Sammy" and r.json()["me"] is True
    r = api.patch(f"/api/v1/household/people/{sam}", json={"role": "admin"}, headers=auth)
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["me"] is False
    r = api.patch(f"/api/v1/household/people/{sam}", json={"view_only": True}, headers=auth)
    assert r.status_code == 422  # an admin is never view-only
    r = api.patch(f"/api/v1/household/people/{sam}", json={"name": "Kim"}, headers=auth)
    assert r.status_code == 409 and r.json()["error"] == "name_taken"
    assert api.post("/api/v1/household/invites", json={"role": "admin", "view_only": True},
                    headers=auth).status_code == 422
    assert api.patch(f"/api/v1/household/people/{sam}", json={}, headers=auth).status_code == 422


def test_a_home_keeps_one_admin(api: TestClient, conn: sqlite3.Connection) -> None:
    _, auth = join(api, conn)
    [admin] = api.get("/api/v1/household/people", headers=auth).json()
    r = api.patch(f"/api/v1/household/people/{admin['id']}", json={"role": "member"}, headers=auth)
    assert r.status_code == 409 and r.json()["error"] == "last_admin"
    r = api.delete(f"/api/v1/household/people/{admin['id']}", headers=auth)
    assert r.status_code == 409 and r.json()["error"] == "last_admin"


def test_removing_someone_ends_their_access_and_their_jobs(api: TestClient, conn: sqlite3.Connection) -> None:
    hid, auth = join(api, conn)
    aid = add_appliance(conn, hid)
    sam, member = joined(api, auth, "Sam")
    kim, other = joined(api, auth, "Kim")
    assert api.delete(f"/api/v1/household/people/{kim}", headers=member).status_code == 403  # not theirs to remove
    r = api.put(f"/api/v1/appliances/{aid}/jobs/{JOB}/assignee", json={"person_id": sam}, headers=member)
    assert r.status_code == 200 and r.json()["assignee"] == sam
    DUE.validate(r.json())
    assert api.delete(f"/api/v1/household/people/{sam}", headers=auth).status_code == 204
    assert api.get("/api/v1/due", headers=member).status_code == 401  # their token is revoked
    assert api.get("/api/v1/due", headers=auth).json()[0]["assignee"] is None  # their job is unassigned
    assert api.delete(f"/api/v1/household/people/{kim}", headers=other).status_code == 204  # leaving
    assert [p["name"] for p in api.get("/api/v1/household/people", headers=auth).json()] == ["Admin"]


def test_assigning_a_job(api: TestClient, conn: sqlite3.Connection) -> None:
    hid, auth = join(api, conn)
    aid = add_appliance(conn, hid)
    sam, member = joined(api, auth, "Sam")
    _, viewer = joined(api, auth, "Kim", view_only=True)
    url = f"/api/v1/appliances/{aid}/jobs/{JOB}/assignee"
    assert api.put(url, json={"person_id": sam}, headers=viewer).status_code == 403  # a viewer reads only
    assert api.put(url, json={"person_id": sam}, headers=token(api, auth, "ha", "read")).status_code == 403
    other_hid, other = join(api, conn)
    [stranger] = api.get("/api/v1/household/people", headers=other).json()
    assert api.put(url, json={"person_id": stranger["id"]}, headers=auth).status_code == 422  # not in this home
    assert api.put(url, json={"person_id": sam}, headers=token(api, auth, "agent", "write")).json()["assignee"] == sam
    changes = api.get(f"/api/v1/appliances/{aid}/jobs/{JOB}/changes", headers=auth).json()
    assert [c["kind"] for c in changes] == ["assignee"]
    assert api.post(f"/api/v1/appliances/{aid}/jobs/{JOB}/undo", headers=auth).json()["assignee"] is None
    assert api.put(url, json={"person_id": None}, headers=member).json()["assignee"] is None
    assert api.put(url, json={"person_id": sam, "x": 1}, headers=member).status_code == 422
    assert other_hid != hid


def test_a_viewer_changes_nothing_on_the_schedule(api: TestClient, conn: sqlite3.Connection) -> None:
    hid, auth = join(api, conn)
    aid = add_appliance(conn, hid)
    _, viewer = joined(api, auth, "Kim", view_only=True)
    assert api.get("/api/v1/due", headers=viewer).status_code == 200
    for method, path, body in (("post", "done", None), ("put", "schedule", {"kind": "manual"}),
                               ("put", "last-done", {"source": "never"}), ("post", "undo", None)):
        r = api.request(method.upper(), f"/api/v1/appliances/{aid}/jobs/{JOB}/{path}", json=body, headers=viewer)
        assert r.status_code == 403, (path, r.text)
    assert re.search("view", api.post(f"/api/v1/appliances/{aid}/jobs/{JOB}/done", headers=viewer).json()["detail"])


ALERTS = Draft202012Validator({"$ref": "urn:api#/components/schemas/Alerts"}, registry=REG)
DEFAULTS = {"push": True, "calendar": True, "hub": True, "items": "all", "follows": [], "quiet": None}


def test_each_person_sets_their_own_alerts(api: TestClient, conn: sqlite3.Connection) -> None:
    hid, auth = join(api, conn)
    aid = add_appliance(conn, hid)
    _, viewer = joined(api, auth, "Kim", view_only=True)
    assert api.get("/api/v1/me/alerts", headers=viewer).json() == DEFAULTS
    ALERTS.validate(DEFAULTS)
    mine = {"push": False, "calendar": True, "hub": False, "items": "chosen", "follows": [aid],
            "quiet": {"from": "22:00", "to": "07:30"}}
    r = api.put("/api/v1/me/alerts", json=mine, headers=viewer)  # a viewer sets their own alerts too
    assert r.status_code == 200 and r.json() == mine
    ALERTS.validate(r.json())
    assert api.get("/api/v1/me/alerts", headers=viewer).json() == mine
    assert api.get("/api/v1/me/alerts", headers=auth).json() == DEFAULTS  # the admin's are their own
    back = api.put("/api/v1/me/alerts", json=DEFAULTS, headers=viewer)
    assert back.json() == DEFAULTS


def test_alerts_refuse_what_is_not_a_person_or_not_this_home(api: TestClient, conn: sqlite3.Connection) -> None:
    hid, auth = join(api, conn)
    other_hid, _ = join(api, conn)
    theirs = add_appliance(conn, other_hid)
    for kind in ("agent", "ha"):
        who = token(api, auth, kind, "write")
        assert api.get("/api/v1/me/alerts", headers=who).status_code == 403
        assert api.put("/api/v1/me/alerts", json=DEFAULTS, headers=who).status_code == 403
    r = api.put("/api/v1/me/alerts", json={**DEFAULTS, "items": "chosen", "follows": [theirs]}, headers=auth)
    assert r.status_code == 422 and "not in this home" in r.json()["detail"]
    for bad in ({**DEFAULTS, "quiet": {"from": "25:00", "to": "07:00"}}, {**DEFAULTS, "x": 1},
                {k: v for k, v in DEFAULTS.items() if k != "hub"}):
        assert api.put("/api/v1/me/alerts", json=bad, headers=auth).status_code == 422
    assert hid != other_hid


def test_only_an_admin_deletes_the_home(api: TestClient, conn: sqlite3.Connection) -> None:
    hid, auth = join(api, conn)
    _, member = joined(api, auth, "Sam")
    _, viewer = joined(api, auth, "Kim", view_only=True)
    for who in (member, viewer, token(api, auth, "agent", "write"), token(api, auth, "ha", "write")):
        assert api.delete("/api/v1/household", headers=who).status_code == 403
    assert conn.execute("SELECT deleted_at FROM households WHERE id = ?", (hid,)).fetchone()[0] is None
    assert api.delete("/api/v1/household", headers=auth).status_code == 204
