"""Who may use the appliance routes and open a session: the household's own token and a person's app token (a phone
back from a return link) read and change; a view-only person reads, and a write is 403; a removed person, a feed
token or another home's token gets 401; the home's agent and Home Assistant tokens only read. A session starts for
the household's own token or a person who may change things, never for a view-only person."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from app.schedule import store as l5
from tests.l1.test_api import SECRET, auth, world  # noqa: F401
from tests.l1.test_library import bearer, person
from tests.l1.test_server import hello


def test_each_kind_of_token_on_the_appliance_routes(world: Any) -> None:  # noqa: F811
    tc, conn, ids = world
    a, wa = ids["a"], ids["wa"]
    member, viewer, gone = person(conn, a), person(conn, a, view_only=True), person(conn, a, role="admin")
    conn.execute("UPDATE people SET removed_at = '2026-10-27T12:00:00+00:00' WHERE id = "
                 "(SELECT person_id FROM household_tokens WHERE token_sha256 = ?)",
                 (hashlib.sha256(gone.encode()).hexdigest(),))
    agent = l5.issue_token(conn, a, by_kind="agent", scope="write", label="agent")[1]
    feed = l5.issue_token(conn, a, by_kind="feed", scope="read", label="cal")[1]
    theirs = person(conn, ids["b"])

    def status(h: dict[str, str]) -> tuple[int, int, int]:
        got = tc.get(f"/api/v1/appliances/{wa}", headers=h).status_code
        listed = tc.get("/api/v1/appliances", headers=h).status_code
        # a body with no model or plate: past the door it is 422 (and no ingest starts), at the door 401 or 403
        wrote = tc.post("/api/v1/appliances", headers=h, json={"alias": "dw", "kind": "dishwasher"}).status_code
        return got, listed, wrote

    assert status(auth(a)) == status(bearer(member)) == (200, 200, 422)
    assert status(bearer(viewer)) == (200, 200, 403)
    assert tc.post(f"/api/v1/appliances/{wa}/manual", headers={**bearer(viewer), "content-type": "application/pdf"},
                   content=b"%PDF").status_code == 403
    assert status(bearer(gone)) == (401, 401, 401)
    assert status(bearer(agent)) == (200, 200, 401)
    assert status(bearer(feed)) == (401, 401, 401)
    assert tc.get(f"/api/v1/appliances/{wa}", headers=bearer(theirs)).status_code == 404  # another home's: not found


@pytest.mark.parametrize(("view_only", "starts"), [(False, True), (True, False)])
def test_a_persons_app_token_opens_a_session_unless_view_only(world: Any, view_only: bool,  # noqa: F811
                                                               starts: bool) -> None:
    tc, conn, ids = world
    token = person(conn, ids["a"], view_only=view_only)
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello(household=token)))
        if starts:
            ok = sock.receive_json()
            assert ok["type"] == "hello_ok"
            assert tc.app.state.registry.sessions[ok["session_id"]].household == ids["a"]  # type: ignore[attr-defined]
        else:
            with pytest.raises(WebSocketDisconnect) as e:
                sock.receive_json()
            assert e.value.code == 1008
