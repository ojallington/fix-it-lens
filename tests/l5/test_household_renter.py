"""GET and PATCH /household (contracts 1.23): renter is null until someone in the home says, then true or false; only
the home's admin changes it, never an agent's or Home Assistant's token."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from tests.l5.conftest import ROOT, T0
from tests.l5.test_manage import token
from tests.l5.test_pilot_door import api, clock, deps, join  # noqa: F401 (fixtures)

SPEC = yaml.safe_load((ROOT / "contracts/api.openapi.yaml").read_text())
HOUSEHOLD = Draft202012Validator({"$ref": "urn:api#/components/schemas/Household"},
                                 registry=Registry().with_resource("urn:api", Resource.opaque(SPEC)))


def test_renter_is_null_until_the_admin_says(api: TestClient, conn: sqlite3.Connection) -> None:  # noqa: F811
    _, auth = join(api, conn)
    _, other = join(api, conn)
    got = api.get("/api/v1/household", headers=auth).json()
    HOUSEHOLD.validate(got)
    assert got["renter"] is None and got["name"] is None
    for value in (True, False, None):
        r = api.patch("/api/v1/household", json={"renter": value}, headers=auth)
        assert r.status_code == 200 and r.json()["renter"] is value
        HOUSEHOLD.validate(r.json())
        assert api.get("/api/v1/household", headers=auth).json()["renter"] is value
    api.patch("/api/v1/household", json={"renter": True}, headers=auth)
    assert api.get("/api/v1/household", headers=other).json()["renter"] is None  # one home's answer only


def member(conn: sqlite3.Connection, hid: str) -> dict[str, str]:
    """A member of the home (not an admin) with their own app token."""
    pid, tok = "p_" + secrets.token_hex(4), "fxt_" + secrets.token_urlsafe(16)
    conn.execute("INSERT INTO people (id, household_id, name, role, created_at) VALUES (?, ?, 'Sam', 'member', ?)",
                 (pid, hid, T0.isoformat()))
    conn.execute("INSERT INTO household_tokens (id, household_id, by_kind, scope, person_id, label, token_sha256,"
                 " created_at) VALUES (?, ?, 'app', 'write', ?, 'phone', ?, ?)",
                 ("t_" + pid, hid, pid, hashlib.sha256(tok.encode()).hexdigest(), T0.isoformat()))
    return {"Authorization": f"Bearer {tok}"}


def test_only_the_admin_changes_it(api: TestClient, conn: sqlite3.Connection) -> None:  # noqa: F811
    hid, auth = join(api, conn)
    others = [token(api, auth, kind, "write") for kind in ("agent", "ha")] + [member(conn, hid)]
    assert api.get("/api/v1/household", headers=others[-1]).status_code == 200  # a member reads it
    for other in others:
        assert api.patch("/api/v1/household", json={"renter": True}, headers=other).status_code == 403
    assert api.patch("/api/v1/household", json={}, headers=auth).status_code == 422
    assert api.patch("/api/v1/household", json={"renter": True, "country": "de"}, headers=auth).status_code == 422
    assert api.get("/api/v1/household", headers=auth).json()["renter"] is None
