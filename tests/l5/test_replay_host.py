"""The replay-only host (deploy/replay_host.py) plays curated recordings and answers nothing else."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.recorder import Recorder
from contracts.models.session import SessionMeta
from deploy import replay_host


def recording(root: Path, sid: str) -> None:
    meta = SessionMeta(session_id=sid, started_at=datetime(2026, 10, 27, tzinfo=UTC), mode="capture", lang="en",
                       contracts_version="1.8.0")
    rec = Recorder(root, meta)
    rec.client(0, "c2s", {"type": "frame", "frame_id": "f0", "t_ms": 0, "jpeg_1024": "b64"})
    rec.frame("f0", b"\xff\xd8 frame")
    rec.client(10, "s2c", {"type": "say", "seq": 1, "t_ms": 10, "text": "recorded line"})
    rec.end("done")


class Clock:
    """The rate limit's time, moved only by the test: a slow run never refills the bucket."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> TestClient:
    monkeypatch.setenv("FIXIT_CALLS", "replay")
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "unused"))
    recording(tmp_path / "replays", "demo")
    recording(tmp_path / "replays", "second")
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Fix-It Lens</title>")
    (dist / "sw.js").write_text("// sw")
    (dist / "assets" / "app.js").write_text("// app")
    return TestClient(replay_host.build(tmp_path / "replays", dist, "demo", per_min=50, clock=clock),
                      follow_redirects=False)


def test_it_plays_a_recording(host: TestClient) -> None:
    r = host.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/?replay=demo"
    page = host.get("/?replay=second")
    assert page.status_code == 200 and "Fix-It Lens" in page.text and page.headers["x-robots-tag"] == "noindex"
    for path in ("/sw.js", "/assets/app.js"):
        assert host.get(path).status_code == 200
    rec = host.get("/api/v1/replay/demo").json()
    assert rec["meta"]["session_id"] == "demo" and [e["dir"] for e in rec["events"]] == ["c2s", "s2c"]
    frame = host.get("/api/v1/replay/demo/frames/f0.jpg")
    assert frame.status_code == 200 and frame.content == b"\xff\xd8 frame"
    assert host.get("/api/v1/replay/nope").status_code == 404


@pytest.mark.parametrize("path", [
    "/preflight", "/api/v1/preflight", "/api/v1/household", "/api/v1/due", "/api/v1/appliances",
    "/api/v1/sessions/demo/pages/1.png", "/api/v1/push/vapid-public-key", "/docs", "/openapi.json", "/redoc",
    "/anything-else", "/../pyproject.toml",
])
def test_nothing_else_is_answered(host: TestClient, path: str) -> None:
    r = host.get(path)
    assert r.status_code == 404 and "only plays recordings" in r.text


def test_no_writes_no_socket(host: TestClient) -> None:
    assert host.post("/api/v1/households", json={"invite_code": "X", "consent": True}).status_code == 404
    assert host.post("/api/v1/preflight/warm-up").status_code == 404
    assert host.put("/api/v1/replay/demo").status_code == 404
    with pytest.raises(WebSocketDisconnect), host.websocket_connect("/ws") as w:
        w.receive_text()
    assert not any(Path(os.environ["FIXIT_PRIVATE"]).iterdir())  # no store, no secret, nothing private made


def test_rate_limited(host: TestClient, clock: Clock) -> None:
    codes = [host.get("/sw.js").status_code for _ in range(60)]
    assert codes[:50] == [200] * 50 and set(codes[50:]) == {429}
    assert host.get("/sw.js").headers["retry-after"] == "60"
    clock.now += 1.2  # 50 a minute: one more request a little over a second later
    assert host.get("/sw.js").status_code == 200 and host.get("/sw.js").status_code == 429


def test_replay_mode_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recording(tmp_path / "replays", "demo")
    monkeypatch.setenv("FIXIT_CALLS", "record")
    with pytest.raises(SystemExit, match="replay mode only"):
        replay_host.build(tmp_path / "replays", tmp_path)
    monkeypatch.setenv("FIXIT_CALLS", "replay")
    with pytest.raises(SystemExit, match="no recordings"):
        replay_host.build(tmp_path / "empty", tmp_path)
    with pytest.raises(SystemExit, match="not in"):
        replay_host.build(tmp_path / "replays", tmp_path, "other")


def test_bucket_refills() -> None:
    b = replay_host.Bucket(60)
    assert all(b.take("a", now=0.0) for _ in range(60)) and not b.take("a", now=0.0)
    assert b.take("a", now=1.5) and b.take("b", now=0.0)


def test_golden_is_the_default_and_names_only_a_curated_recording(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_CALLS", "replay")
    monkeypatch.delenv("FIXIT_REPLAY_GOLDEN", raising=False)
    replays, dist = tmp_path / "replays", tmp_path / "dist"
    recording(replays, "demo")
    recording(replays, "m1")
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    with pytest.raises(SystemExit, match="no .*GOLDEN"):
        replay_host.build(replays, dist, "golden")
    (replays / "GOLDEN").write_text("m1\n")
    host = TestClient(replay_host.build(replays, dist), follow_redirects=False)
    assert host.get("/").headers["location"] == "/?replay=golden"   # the link stays when GOLDEN changes
    (replays / "GOLDEN").write_text("raw-private-session\n")         # not a curated recording here
    with pytest.raises(SystemExit, match="not a recording"):
        replay_host.build(replays, dist)
    monkeypatch.setenv("FIXIT_REPLAY_GOLDEN", "demo")                # the env wins, as in the app
    assert replay_host.golden(replays) == "demo"
