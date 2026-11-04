"""The README's "Try it now" link (?replay=demo) plays wherever it points: the served app and the replay host both
play the repo's demo/replays beside their own recordings (deploy/more_replays.py)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import calls
from app.server import create_app
from deploy import replay_host, serve
from deploy.more_replays import DEMO, MoreReplays, recordings
from tests.l5.test_replay_host import recording

FRAMES = sorted(p.name for p in (DEMO / "demo/frames").glob("*.jpg"))


def test_the_repo_has_the_demo() -> None:
    assert "demo" in recordings(DEMO) and FRAMES


def test_the_served_app_plays_the_demo_and_its_own(tmp_path: Path) -> None:
    recording(tmp_path / "sessions", "s_own")
    app = create_app(client=calls.make_client("replay", private=None), record_root=tmp_path / "sessions")
    c = TestClient(MoreReplays(app, [DEMO]))
    demo = c.get("/api/v1/replay/demo")
    assert demo.status_code == 200 and demo.json()["meta"]["session_id"] == "demo" and demo.json()["events"]
    frame = c.get(f"/api/v1/replay/demo/frames/{FRAMES[0]}")
    assert frame.status_code == 200 and frame.content == (DEMO / "demo/frames" / FRAMES[0]).read_bytes()
    assert c.get("/api/v1/replay/s_own").status_code == 404  # its own record names no home: served to nobody
    assert c.get("/api/v1/replay/nope").status_code == 404
    assert c.post("/api/v1/replay/demo").status_code != 200  # only reads reach the demo folder
    without = TestClient(app)
    assert without.get("/api/v1/replay/demo").status_code == 404  # the finding: the served app alone has no demo


def test_golden_is_the_curated_folders_own(tmp_path: Path) -> None:
    recording(tmp_path / "sessions", "s_own")
    recording(tmp_path / "curated", "c1")
    app = create_app(client=calls.make_client("replay", private=None), record_root=tmp_path / "sessions")
    assert TestClient(MoreReplays(app, [tmp_path / "curated"])).get("/api/v1/replay/golden").status_code == 404
    (tmp_path / "curated" / "GOLDEN").write_text("c1\n")
    c = TestClient(MoreReplays(app, [tmp_path / "curated"]))
    golden = c.get("/api/v1/replay/golden")
    assert golden.status_code == 200 and golden.json()["replay_id"] == "c1"


def test_the_replay_host_plays_the_demo_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_CALLS", "replay")
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "unused"))
    recording(tmp_path / "replays", "rehearsal")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Fix-It Lens</title>")
    c = TestClient(replay_host.build(tmp_path / "replays", dist, more=[DEMO]), follow_redirects=False)
    assert c.get("/").headers["location"] == "/?replay=rehearsal"
    assert c.get("/api/v1/replay/demo").status_code == 200 and c.get("/api/v1/replay/rehearsal").status_code == 200
    assert c.get(f"/api/v1/replay/demo/frames/{FRAMES[0]}").headers["x-robots-tag"] == "noindex"
    assert c.get("/api/v1/preflight").status_code == 404  # still nothing but replays


@pytest.mark.parametrize("replay_only", [False, True])
def test_serve_puts_the_demo_on_both_doors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay_only: bool) -> None:
    import uvicorn

    import app.server

    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda a, **kw: ran.update(app=a))
    monkeypatch.setattr(app.server, "production_app", lambda dist: FastAPI())
    monkeypatch.setenv("FIXIT_CALLS", "replay")
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "unused"))
    recording(tmp_path / "replays", "rehearsal")
    argv = ["serve.py", "--dist", str(tmp_path)] + (["--replay-only", "--replays", str(tmp_path / "replays")]
                                                    if replay_only else [])
    monkeypatch.setattr(sys, "argv", argv)
    serve.main()
    assert TestClient(ran["app"]).get("/api/v1/replay/demo").status_code == 200
