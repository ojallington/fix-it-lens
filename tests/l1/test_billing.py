"""Who pays: a session, a plate read and an add bill to the household's ledger lane (app.feeds.caps.household_lane):
'pilot' for a cohort home, the server's own lane for one of our own homes. Never config.LANE alone."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import calls
from app.schedule import store as l5
from app.server import create_app
from app.store import Store
from tests.l1.test_api import BOSCH, SECRET, FakeIngest, auth, ingested
from tests.l1.test_server import hello
from tests.l5.conftest import add_appliance, add_household


def own(conn: sqlite3.Connection, hid: str) -> None:
    with conn:
        conn.execute("UPDATE households SET is_own = 1 WHERE id = ?", (hid,))


@pytest.mark.parametrize(("is_own", "lane"), [(False, "pilot"), (True, "l1")])
def test_an_add_bills_the_households_lane(tmp_path: Path, is_own: bool, lane: str) -> None:
    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    if is_own:
        own(conn, hid)
    fake = FakeIngest(ingested("checked", card_path=Path(BOSCH).resolve()))
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    with TestClient(create_app(client=client, record_root=tmp_path / "s", store=Store(conn, SECRET),
                               ingest=fake)) as tc:
        assert tc.post("/api/v1/appliances", headers=auth(hid), json={"model": "SMV4HAX40N"}).status_code == 201
        tc.get("/api/v1/appliances", headers=auth(hid))
    assert fake.calls[0]["lane"] == lane


@pytest.mark.parametrize(("is_own", "lane"), [(False, "pilot"), (True, "l1")])
def test_a_session_bills_the_households_lane(tmp_path: Path, is_own: bool, lane: str) -> None:
    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    if is_own:
        own(conn, hid)
    aid = add_appliance(conn, hid, "dishwasher", BOSCH)
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    app = create_app(client=client, record_root=tmp_path / "sessions", store=Store(conn, SECRET))
    with TestClient(app).websocket_connect("/ws") as sock:
        sock.send_text(json.dumps(hello(household=l5.household_token(SECRET, hid), appliance_id=aid,
                                        job_id="clean_filters")))
        ok = sock.receive_json()
        assert ok["type"] == "hello_ok"
        s = app.state.registry.sessions[ok["session_id"]]
        assert s.lane == lane and s.models.lane == lane and s.models._kw()["lane"] == lane
