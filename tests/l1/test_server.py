"""Capture mode records frames with no model calls; the WS resumes by seq; frames are
latest-wins; replay plays a recording with the REPLAY badge first."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import calls
from app import recorder
from app.server import create_app, outcome_of
from contracts.models import ws
from tests.l1.helpers import jpeg


def hello(**kw: Any) -> dict[str, Any]:
    return {"type": "hello", "seq": 0, "t_ms": 0, "session_id": None, "last_seq": None, "client": "phone",
            "lang": "en", **kw}


def frame_msg(i: int, t_ms: float) -> dict[str, Any]:
    return {"type": "frame", "seq": i, "t_ms": t_ms, "frame_id": f"f{i}",
            "jpeg_1024": base64.b64encode(jpeg(shade=60 + i)).decode(), "phash": f"{i:016x}"}


def app_for(tmp_path: Path) -> tuple[TestClient, calls.Client]:
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    return TestClient(create_app(client=client, record_root=tmp_path / "sessions")), client


def curated(tmp_path: Path, client: calls.Client) -> TestClient:
    """A player of the same folder as a curated one (the replay host's, deploy/curate.py's --into)."""
    return TestClient(create_app(client=client, replay_root=tmp_path / "sessions"))


def test_capture_records_frames_and_calls_nothing(tmp_path: Path) -> None:
    tc, client = app_for(tmp_path)
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello()))
        ok = sock.receive_json()
        assert ok["type"] == "hello_ok" and ok["resumed"] is False
        for i in range(1, 6):
            sock.send_text(json.dumps(frame_msg(i, 100.0 * i)))
        sock.send_text(json.dumps({"type": "ping", "seq": 6, "t_ms": 600}))
        assert sock.receive_json()["type"] == "pong"
    sid = ok["session_id"]
    rec = recorder.load(tmp_path / "sessions", sid)
    assert rec.meta.mode == "capture"
    frames = [x for x in rec.lines if x["kind"] == "frame"]
    assert [x["msg"]["frame_id"] for x in frames] == ["f1", "f2", "f3", "f4", "f5"]
    assert all(x["msg"]["jpeg_1024"] is None for x in frames)  # bytes are files, not event lines
    assert sorted(p.name for p in (rec.dir / "frames").iterdir()) == [f"f{i}.jpg" for i in range(1, 6)]
    assert client.backend.calls == 0 and client.requests == []  # type: ignore[attr-defined]


def test_resume_resends_everything_after_last_seq(tmp_path: Path) -> None:
    tc, _ = app_for(tmp_path)
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello()))
        ok = sock.receive_json()
        for i in range(1, 4):
            sock.send_text(json.dumps({"type": "ping", "seq": i, "t_ms": i}))
            assert sock.receive_json()["seq"] == i
    with tc.websocket_connect("/ws") as sock:
        sock.send_text(json.dumps(hello(session_id=ok["session_id"], last_seq=1)))
        back = sock.receive_json()
        assert back["type"] == "hello_ok" and back["resumed"] is True and back["session_id"] == ok["session_id"]
        missed = [sock.receive_json(), sock.receive_json()]
        assert [m["seq"] for m in missed] == [2, 3] and {m["type"] for m in missed} == {"pong"}
        assert sock.receive_json()["kind"] == "reconnected"


def test_latest_wins_frames() -> None:
    from app.frames import FrameBuffer
    from tests.l1.helpers import frame

    buf = FrameBuffer()
    buf.add(frame(2, 200))
    buf.add(frame(1, 100))  # a late frame never replaces a newer one
    assert buf.latest() is not None and buf.latest().frame_id == "f2"  # type: ignore[union-attr]
    for i in range(3, 80):
        buf.add(frame(i, 100.0 * i))
    assert len(buf.window(100)) <= 70 and buf.get("f2") is None  # the ring drops old frames


def test_replay_plays_the_recording_with_the_badge(tmp_path: Path) -> None:
    tc, client = app_for(tmp_path)
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello()))
        sid = sock.receive_json()["session_id"]
        sock.send_text(json.dumps({"type": "ping", "seq": 1, "t_ms": 1}))
        sock.receive_json()
    play = curated(tmp_path, client)
    with play.websocket_connect("/ws?speed=100") as sock:
        sock.send_text(json.dumps(hello(replay_id=sid)))
        first = sock.receive_json()
        assert first == {"type": "replay", "seq": 0, "t_ms": 0, "badge": True, "replay_id": sid}
        assert sock.receive_json()["type"] == "pong"
    r = play.get(f"/api/v1/replay/{sid}")
    assert r.status_code == 200 and r.json()["meta"]["session_id"] == sid
    assert play.get("/api/v1/replay/..%2Fx").status_code == 404
    assert client.requests == []


def test_a_replay_never_carries_the_household_token(tmp_path: Path) -> None:
    secret = "h_0123456789abcdef-" + "s" * 40
    tc, client = app_for(tmp_path)
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello(household=secret)))
        sid = sock.receive_json()["session_id"]
        sock.send_text(json.dumps({"type": "ping", "seq": 1, "t_ms": 1}))
        sock.receive_json()
    assert secret not in (tmp_path / "sessions" / sid / "events.jsonl").read_text()
    # A record written before the recorder dropped the token, with the household's id in its meta.
    old = tmp_path / "sessions" / "older"
    (old / "frames").mkdir(parents=True)
    (old / "meta.json").write_text(json.dumps({"session_id": "older", "household": "h_0123456789abcdef",
                                               "started_at": "2026-01-01T00:00:00Z", "mode": "api", "lang": "en",
                                               "contracts_version": "1.0.0"}))
    line = {"t_ms": 0, "dir": "c2s", "kind": "hello", "msg": hello(household=secret)}
    (old / "events.jsonl").write_text(json.dumps(line) + "\n")
    r = curated(tmp_path, client).get(f"/api/v1/replay/{sid}")
    assert r.status_code == 200 and secret not in r.text and "household" not in r.json()["meta"]
    assert [e["kind"] for e in r.json()["events"]][0] == "hello"
    assert tc.get("/api/v1/replay/older").status_code == 404  # a home's recording, and no token of that home
    lines = recorder.public_lines(recorder.load(tmp_path / "sessions", "older"))  # what its own home is served
    assert secret not in json.dumps(lines) and lines[0]["kind"] == "hello"


def test_the_devices_timings_go_into_the_record_and_nothing_answers_them(tmp_path: Path) -> None:
    tc, client = app_for(tmp_path)
    timings = {"type": "timings", "seq": 1, "t_ms": 5000, "hands_ms": [11.2, 9.8],
               "stops": [{"frame_id": "f7", "ms": 48.5}]}
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello()))
        sid = sock.receive_json()["session_id"]
        sock.send_text(json.dumps(timings))
        sock.send_text(json.dumps({"type": "ping", "seq": 2, "t_ms": 5001}))
        assert sock.receive_json()["type"] == "pong"
    rec = recorder.load(tmp_path / "sessions", sid)
    got = [x["msg"] for x in rec.lines if x["kind"] == "timings"]
    assert got == [timings]
    assert client.requests == []


def test_the_phones_lines_are_on_the_sessions_clock_so_a_replay_lines_up(tmp_path: Path) -> None:
    tc, _ = app_for(tmp_path)
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello(t_ms=190_000)))  # the phone's clock: three minutes since its page loaded
        sid = sock.receive_json()["session_id"]
        sock.send_text(json.dumps(frame_msg(1, 190_100.0)))
        sock.send_text(json.dumps({"type": "ping", "seq": 2, "t_ms": 190_200}))
        sock.receive_json()
    lines = recorder.load(tmp_path / "sessions", sid).lines
    c2s = [x for x in lines if x["dir"] == "c2s"]
    assert [x["kind"] for x in c2s] == ["hello", "frame", "ping"]
    assert all(x["t_ms"] < 60_000 for x in lines)  # one clock for both directions
    assert [x["msg"]["t_ms"] for x in c2s] == [190_000, 190_100.0, 190_200]  # the phone's own times stay


def test_manual_page_route_serves_only_cited_pages(tmp_path: Path, monkeypatch: Any) -> None:
    from tests.l1.test_session import acme
    from tools.make_acme_manual import build_pdf

    (tmp_path / "manuals/acme").mkdir(parents=True)
    (tmp_path / "manuals/acme/WM-0000.pdf").write_bytes(build_pdf())
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path))
    tc, client = app_for(tmp_path)
    reg = tc.app.state.registry  # type: ignore[attr-defined]
    from app.server import make_session

    s = make_session(client=client, mode="api", record_root=None, hello=ws.Hello.model_validate(hello()), card=acme(),
                     guide=False)
    reg.sessions[s.session_id] = s
    page = acme().jobs[0].steps[0].quote.page
    r = tc.get(f"/api/v1/sessions/{s.session_id}/pages/{page}.png")
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert tc.get(f"/api/v1/sessions/{s.session_id}/pages/99.png").status_code == 404
    assert tc.get(f"/api/v1/sessions/nope/pages/{page}.png").status_code == 404


def test_no_rules_for_the_country_refuses_the_session(tmp_path: Path) -> None:
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    tc = TestClient(create_app(country="zz", client=client, record_root=tmp_path / "sessions"))
    with tc.websocket_connect("/ws?card=acme/wm-0000") as sock:
        sock.send_text(json.dumps(hello()))
        assert sock.receive_json()["kind"] == "no_rules_for_country"
    with tc.websocket_connect("/ws?mode=capture") as sock:  # capture says nothing, so it needs no rules
        sock.send_text(json.dumps(hello()))
        assert sock.receive_json()["type"] == "hello_ok"


def test_the_served_app_records_into_the_private_folder(tmp_path: Path, monkeypatch: Any) -> None:
    from app.server import served_client

    monkeypatch.setenv("FIXIT_CALLS", "record")
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path))
    monkeypatch.setattr(calls, "_default", None)
    client, fallback = served_client()
    assert client.mode == "record" and client.fixtures_root == tmp_path / "fixtures"
    assert calls.default_client() is client  # ingest's plate read and lookup record there too
    assert fallback is None or fallback.fixtures_root == tmp_path / "fixtures"
    monkeypatch.setenv("FIXIT_CALLS", "replay")
    monkeypatch.setattr(calls, "_default", None)
    assert served_client()[0].mode == "replay"


def test_a_replay_names_its_job_and_golden_names_the_curated_one(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("FIXIT_REPLAY_GOLDEN", raising=False)
    tc, client = app_for(tmp_path)
    assert tc.get("/api/v1/replay/golden").status_code == 404  # nothing curated yet
    from datetime import UTC, datetime

    from contracts import VERSION
    from contracts.models.session import SessionMeta

    sid = "bosch1"
    rec = recorder.Recorder(tmp_path / "sessions", SessionMeta(
        session_id=sid, started_at=datetime.now(UTC), mode="api", lang="en", country="nl",
        card="cards/bosch/SMV4HAX40N.yaml", job_id="clean_filters", contracts_version=VERSION))
    rec.client(0, "s2c", {"type": "sentence", "seq": 1, "t_ms": 0, "id": "s1", "text": "Hello."})
    play = curated(tmp_path, client)
    body = play.get(f"/api/v1/replay/{sid}").json()
    assert body["replay_id"] == sid
    assert body["summary"]["appliance_alias"] == "dishwasher" and body["summary"]["steps_total"] >= 3
    assert body["summary"]["job_title"]
    (tmp_path / "sessions" / "GOLDEN").write_text(sid + "\n")
    golden = play.get("/api/v1/replay/golden").json()
    assert golden["replay_id"] == sid and golden["events"] == body["events"]
    with play.websocket_connect("/ws?speed=100") as sock:
        sock.send_text(json.dumps(hello(replay_id="golden")))
        assert sock.receive_json()["replay_id"] == sid
    assert tc.get("/api/v1/replay/golden").status_code == 404  # never from the sessions folder
    monkeypatch.setenv("FIXIT_REPLAY_GOLDEN", "nope")
    assert play.get("/api/v1/replay/golden").status_code == 404 and client.requests == []


def test_a_record_with_no_household_is_served_to_nobody(tmp_path: Path) -> None:
    """Fails closed: a record in the sessions folder that names no household (made before households, or by a
    capture with no token) holds a home's frames all the same, so it plays for nobody, frames and socket included."""
    tc, client = app_for(tmp_path)
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello()))
        sid = sock.receive_json()["session_id"]
        sock.send_text(json.dumps(frame_msg(1, 100.0)))
        sock.send_text(json.dumps({"type": "ping", "seq": 2, "t_ms": 200}))
        sock.receive_json()
    assert recorder.load(tmp_path / "sessions", sid).meta.household is None
    assert (tmp_path / "sessions" / sid / "frames" / "f1.jpg").is_file()
    for auth in ({}, {"Authorization": "Bearer anything"}):
        assert tc.get(f"/api/v1/replay/{sid}", headers=auth).status_code == 404
        assert tc.get(f"/api/v1/replay/{sid}/frames/f1.jpg", headers=auth).status_code == 404
    with tc.websocket_connect("/ws?speed=100") as sock:
        sock.send_text(json.dumps(hello(replay_id=sid)))
        try:
            sock.receive_json()
            closed = None
        except WebSocketDisconnect as e:
            closed = e.code
    assert closed == 1008
    (tmp_path / "sessions" / "GOLDEN").write_text(sid + "\n")
    assert tc.get("/api/v1/replay/golden").status_code == 404
    assert client.requests == []


class Ended:
    """The store's half of an end: which session ended, how, and at what spend."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, float]] = []

    def session_ended(self, s: Any, outcome: str, usd: float = 0.0) -> None:
        self.rows.append((s.session_id, outcome, usd))


def test_end_closes_the_session_and_writes_its_outcome(tmp_path: Path) -> None:
    tc, _ = app_for(tmp_path)
    reg = tc.app.state.registry  # type: ignore[attr-defined]
    reg.store = Ended()
    with tc.websocket_connect("/ws?mode=capture") as sock:
        sock.send_text(json.dumps(hello()))
        ok = sock.receive_json()
        sock.send_text(json.dumps({"type": "tap", "seq": 1, "t_ms": 5, "action": "end"}))
        try:
            sock.receive_json()
            raise AssertionError("the socket stays open after end")
        except WebSocketDisconnect as e:
            assert e.code == 1000
    sid = ok["session_id"]
    assert sid not in reg.sessions and reg.store.rows == [(sid, "ended", 0.0)]  # the person ended it: not abandoned
    lines = recorder.load(tmp_path / "sessions", sid).lines
    ends = [x for x in lines if x["kind"] == "end"]
    assert [x["outcome"] for x in ends] == ["ended"]
    assert lines[-1] is ends[0] and ends[0]["t_ms"] > 0  # the last line, at the session clock, not at 0
    assert ends[0]["t_ms"] >= max(x["t_ms"] for x in lines[:-1])
    with tc.websocket_connect("/ws?mode=capture") as sock:  # an ended session does not resume
        sock.send_text(json.dumps(hello(session_id=sid, last_seq=0)))
        again = sock.receive_json()
        assert again["resumed"] is False and again["session_id"] != sid


def test_a_session_left_without_end_is_ended_as_abandoned(tmp_path: Path, monkeypatch: Any) -> None:
    import time

    from app import config

    monkeypatch.setattr(config, "SESSION_LEFT_S", 0.05)
    tc, _ = app_for(tmp_path)
    reg = tc.app.state.registry  # type: ignore[attr-defined]
    reg.store = Ended()
    with tc:  # one event loop for the session and its reaper
        with tc.websocket_connect("/ws?mode=capture") as sock:
            sock.send_text(json.dumps(hello()))
            sid = sock.receive_json()["session_id"]
        deadline = time.monotonic() + 3
        while sid in reg.sessions and time.monotonic() < deadline:
            time.sleep(0.02)
    assert sid not in reg.sessions and reg.store.rows == [(sid, "abandoned", 0.0)]
    lines = recorder.load(tmp_path / "sessions", sid).lines
    assert lines[-1]["kind"] == "end" and lines[-1]["outcome"] == "abandoned" and lines[-1]["t_ms"] > 0


def test_how_an_ending_session_went() -> None:
    from types import SimpleNamespace

    s: Any = SimpleNamespace(phase="instructed", stopped=False)
    assert (outcome_of(s, by_person=True), outcome_of(s)) == ("ended", "abandoned")
    s.stopped = True  # End from the STOP screen: the stop is what happened
    assert (outcome_of(s, by_person=True), outcome_of(s)) == ("stopped", "stopped")
    s.phase = "job_done"
    assert (outcome_of(s, by_person=True), outcome_of(s)) == ("done", "done")


def test_a_phone_back_in_time_keeps_the_session(tmp_path: Path, monkeypatch: Any) -> None:
    import time

    from app import config

    monkeypatch.setattr(config, "SESSION_LEFT_S", 0.3)
    tc, _ = app_for(tmp_path)
    reg = tc.app.state.registry  # type: ignore[attr-defined]
    with tc:
        with tc.websocket_connect("/ws?mode=capture") as sock:
            sock.send_text(json.dumps(hello()))
            sid = sock.receive_json()["session_id"]
        with tc.websocket_connect("/ws") as sock:
            sock.send_text(json.dumps(hello(session_id=sid, last_seq=0)))
            assert sock.receive_json()["resumed"] is True
            time.sleep(0.5)
            assert sid in reg.sessions  # the reaper was called off when the phone came back
