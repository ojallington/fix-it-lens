"""No live spend without a household (inside create_app): when the calls client
records or runs live, every /ws hello but a replay needs a joined household's token, a resume too; and in any mode a
household's session resumes only with that household's token. Both close with 1008 before a session opens."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import calls
from app.schedule import store as l5
from app.server import create_app
from app.store import Store
from tests.l1.test_api import SECRET
from tests.l1.test_server import hello
from tests.l5.conftest import add_household


@pytest.fixture(params=["live", "record", "replay"])
def world(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[tuple[TestClient, sqlite3.Connection, str]]:
    conn = l5.open_store(":memory:")
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    client.mode = request.param  # the gate reads the mode; capture sessions make no calls, so nothing is spent
    app = create_app(client=client, store=Store(conn, SECRET), record_root=tmp_path / "sessions")
    yield TestClient(app), conn, request.param


def refused(c: TestClient, msg: dict[str, Any]) -> bool:
    with c.websocket_connect("/ws?mode=capture") as w:
        w.send_json(msg)
        try:
            w.receive_json()
        except WebSocketDisconnect as e:
            return e.code == 1008
    return False


def opened(c: TestClient, msg: dict[str, Any]) -> dict[str, Any]:
    with c.websocket_connect("/ws?mode=capture") as w:
        w.send_json(msg)
        out: dict[str, Any] = w.receive_json()
    return out


@pytest.mark.parametrize("household", [None, "", "not-a-token", "h_nobody-" + "0" * 40])
def test_live_needs_a_households_token(world: Any, household: str | None) -> None:
    c, _, mode = world
    if mode == "replay" and not household:
        assert opened(c, hello(household=household))["type"] == "hello_ok"  # fixtures only: nothing to spend
    else:
        assert refused(c, hello(household=household)) and not c.app.state.registry.sessions


def test_a_joined_household_gets_its_session_and_resumes_only_with_its_token(world: Any) -> None:
    c, conn, _ = world
    a, b = add_household(conn), add_household(conn)
    ok = opened(c, hello(household=l5.household_token(SECRET, a)))
    assert ok["type"] == "hello_ok"
    again = hello(session_id=ok["session_id"], last_seq=0)
    assert refused(c, again)  # no token
    assert refused(c, {**again, "household": l5.household_token(SECRET, b)})  # another household's
    assert opened(c, {**again, "household": l5.household_token(SECRET, a)})["resumed"] is True


def test_a_homes_recording_plays_only_for_that_home_and_is_never_golden(world: Any, tmp_path: Path) -> None:
    from tests.l1.test_server import frame_msg

    c, conn, _ = world
    a, b = add_household(conn), add_household(conn)
    mine, theirs = l5.household_token(SECRET, a), l5.household_token(SECRET, b)
    with c.websocket_connect("/ws?mode=capture") as w:
        w.send_json(hello(household=mine))
        sid = w.receive_json()["session_id"]
        w.send_json(frame_msg(1, 100.0))
        w.send_json({"type": "ping", "seq": 2, "t_ms": 200})
        w.receive_json()
    rec, frame = f"/api/v1/replay/{sid}", f"/api/v1/replay/{sid}/frames/f1.jpg"
    for auth in ({}, {"Authorization": f"Bearer {theirs}"}, {"Authorization": "Bearer not-a-token"}):
        assert c.get(rec, headers=auth).status_code == 404  # the same answer as no recording at all
        assert c.get(frame, headers=auth).status_code == 404
    own = {"Authorization": f"Bearer {mine}"}
    assert c.get(rec, headers=own).status_code == 200 and c.get(frame, headers=own).status_code == 200
    assert refused(c, hello(replay_id=sid)) and refused(c, hello(replay_id=sid, household=theirs))
    with c.websocket_connect("/ws?speed=100") as w:
        w.send_json(hello(replay_id=sid, household=mine))
        assert w.receive_json()["badge"] is True
    (tmp_path / "sessions" / "GOLDEN").write_text(sid + "\n")  # a home's own frames are never shown to everyone
    assert c.get("/api/v1/replay/golden", headers=own).status_code == 404
