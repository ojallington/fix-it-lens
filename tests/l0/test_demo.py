"""make demo: the shipped replay is valid, regenerates exactly, and plays through the real server with no key."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import contracts
from app import recorder
from deploy.demo import demo_app

ROOT = Path(__file__).resolve().parents[2]
REPLAYS = ROOT / "demo/replays"


def test_demo_replay_is_valid_and_has_its_frames() -> None:
    rec = recorder.load(REPLAYS, "demo")
    kinds = [x["msg"]["type"] for x in rec.lines if x["dir"] == "s2c"]
    assert {"step", "outline", "checking", "verdict", "zone", "safety"} <= set(kinds)
    assert kinds[-1] == "safety"  # it ends on the stop beat
    for line in rec.lines:
        if line["dir"] == "s2c":
            contracts.validate("ws", line["msg"])
        elif line["kind"] == "frame":
            assert rec.frame_bytes(line["msg"]["frame_id"]), line["msg"]["frame_id"]
    assert (REPLAYS / "demo/CREDITS.md").exists()


def test_demo_replay_regenerates_identically() -> None:
    import tools.make_demo_replay as gen

    assert "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in gen.events()) == \
        (REPLAYS / "demo/events.jsonl").read_text()


def test_demo_server_serves_the_replay_without_a_private_folder(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("FIXIT_PRIVATE", raising=False)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Fix-It Lens</title>")
    c = TestClient(demo_app(REPLAYS, dist))
    r = c.get("/api/v1/replay/demo")
    assert r.status_code == 200 and r.json()["meta"]["session_id"] == "demo"
    assert c.get("/api/v1/replay/demo/frames/f_front.jpg").headers["content-type"] == "image/jpeg"
    assert c.get("/api/v1/replay/nope").status_code == 404
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "seq": 0, "t_ms": 0, "session_id": None, "last_seq": None, "household": None,
                      "client": "phone", "lang": "en",
                      "replay_id": "demo"})
        assert ws.receive_json()["type"] == "replay"


def test_every_demo_frame_is_credited() -> None:
    for d in REPLAYS.iterdir():
        credits = (d / "CREDITS.md").read_text()
        for f in (d / "frames").glob("*"):
            assert f"frames/{f.name}" in credits, f"{f} has no line in {d.name}/CREDITS.md"


def test_demo_frames_are_the_ones_checked_for_marks() -> None:
    """Only frames a person checked for brand marks, text, faces and house identifiers ship (tools/make_demo_replay.py
    FRAMES): a new or changed frame fails here until it is checked and its sha256 pinned."""
    import hashlib

    import tools.make_demo_replay as gen

    frames = {f.stem: hashlib.sha256(f.read_bytes()).hexdigest() for f in (REPLAYS / "demo/frames").glob("*.jpg")}
    assert frames == {fid: sha for fid, (_file, _licence, _box, sha) in gen.FRAMES.items()}
