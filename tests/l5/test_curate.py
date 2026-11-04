"""deploy/curate.py: a recording reaches the public replays folder only through a staged, reviewed, approved copy."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from deploy import curate

TOKEN = "h_0123456789abcdef-deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def jpeg(shade: int) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (200, 100), (shade, shade, shade))
    im.paste((255, 255, 255), (0, 0, 40, 40))  # a bright corner, so a blur of it shows
    exif = Image.Exif()
    exif[0x010F] = "SomeCamera"
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


@pytest.fixture
def session(tmp_path: Path) -> Path:
    d = tmp_path / "sessions" / "AbCdEf123456"
    (d / "frames").mkdir(parents=True)
    (d / "audio").mkdir()
    (d / "audio" / "u1.wav").write_bytes(b"RIFF voice")
    for i in range(3):
        (d / "frames" / f"f{i}.jpg").write_bytes(jpeg(40 + i))
    (d / "meta.json").write_text(json.dumps({"session_id": "AbCdEf123456", "household": "h_0123456789abcdef",
                                             "appliance_id": "ap_1", "mode": "api", "lang": "en",
                                             "started_at": "2026-10-27T17:00:00Z", "contracts_version": "1.9.0"}))
    lines = [
        {"t_ms": 1, "dir": "c2s", "kind": "hello", "msg": {"type": "hello", "household": TOKEN,
                                                          "session_id": "AbCdEf123456"}},
        {"t_ms": 2, "dir": "c2s", "kind": "plate_photo", "msg": {"type": "plate_photo", "photo_id": "p1"}},
        {"t_ms": 3, "dir": "s2c", "kind": "plate_read", "msg": {"type": "plate_read", "fields": {"serial": "123"}}},
        {"t_ms": 4, "dir": "c2s", "kind": "frame", "msg": {"type": "frame", "frame_id": "f0",
                                                          "phash": "0000000000000001"}},
        {"t_ms": 5, "dir": "c2s", "kind": "utterance", "msg": {"type": "utterance", "audio_wav_b64": None,
                                                              "text": "we live at Elmstreet 12, 1234 AB, me@x.nl"}},
        {"t_ms": 6, "dir": "s2c", "kind": "sentence", "msg": {"type": "sentence", "text": "Turn the filter left."}},
    ]
    (d / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    return d


@pytest.fixture
def private(tmp_path: Path) -> Path:
    p = tmp_path / "private"
    p.mkdir()
    (p / "identifiers.txt").write_text("# house identifiers\nElmstreet\n")
    return p


class Reply:
    def __init__(self, found: dict[str, Any]) -> None:
        self.found = found

    def parsed(self) -> dict[str, Any]:
        return self.found


class FakeModel:
    """f0: a face it can box; f1: a plate it cannot box; f2: nothing, but the street name is legible."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    async def respond(self, role: str, input: Any, **kw: Any) -> Reply:
        self.calls.append({"role": role, **kw})
        if self.fail:
            raise TimeoutError
        shade = Image.open(io.BytesIO(kw["images"][0])).getpixel((150, 50))[0]
        empty: dict[str, Any] = {"faces": [], "identifiers": [], "plates": [], "brands": [], "text_seen": []}
        if shade < 41:
            return Reply({**empty, "faces": [{"what": "face", "box": {"x": 0, "y": 0, "w": 0.2, "h": 0.4}}]})
        if shade < 42:
            return Reply({**empty, "plates": [{"what": "rating plate", "box": None}]})
        return Reply({**empty, "text_seen": ["ELMSTREET"]})


def run(session: Path, private: Path, model: FakeModel, blur: list[str] = ()) -> curate.Staged:
    import asyncio
    return asyncio.run(curate.stage(session, "demo", private, model, list(blur)))


def test_it_checks_every_frame_and_blurs_what_it_finds(session: Path, private: Path) -> None:
    model = FakeModel()
    s = run(session, private, model)
    assert len(model.calls) == 3 and {c["role"] for c in model.calls} == {"judge"}
    assert all(c["lane"] == "l5" and c["format"]["name"] == "PublicFrameCheck" for c in model.calls)
    by = {r.frame_id: r for r in s.frames}
    assert by["f0"].boxes and not by["f0"].whole            # a boxed face: that box
    assert by["f1"].whole                                     # a plate it could not box: the whole frame
    assert by["f2"].whole and "identifiers.txt line 1" in by["f2"].flags[0]  # no line text leaks into the report
    im = Image.open(io.BytesIO((s.dir / "frames/f0.jpg").read_bytes()))
    assert abs(im.getpixel((38, 20))[0] - im.getpixel((42, 20))[0]) < 100  # the bright corner's edge is gone
    for f in ("f0", "f1", "f2"):
        assert not Image.open(s.dir / f"frames/{f}.jpg").getexif()  # re-encoded: no camera metadata


def test_the_events_lose_everything_private(session: Path, private: Path) -> None:
    s = run(session, private, FakeModel())
    text = (s.dir / "events.jsonl").read_text()
    assert TOKEN not in text and "h_0123456789abcdef" not in text
    assert "plate_photo" not in text and "plate_read" not in text
    assert "Elmstreet" not in text and "1234 AB" not in text and "me@x.nl" not in text
    assert "0000000000000001" in text and "Turn the filter left." in text  # hashes and the guide's words stay
    meta = json.loads((s.dir / "meta.json").read_text())
    assert meta["session_id"] == "demo" and "household" not in meta and "appliance_id" not in meta
    assert not (s.dir / "audio").exists()
    report = (s.dir / "report.json").read_text()
    assert "Elmstreet" not in report and "line 5 (utterance)" in report
    assert s.digest in (s.dir / "review.html").read_text()


def test_a_failed_check_blurs_whole_and_is_retried(session: Path, private: Path) -> None:
    s = run(session, private, FakeModel(fail=True))
    assert all(r.whole and r.failed for r in s.frames)
    again = FakeModel()
    run(session, private, again)
    assert len(again.calls) == 3  # failures are asked again
    third = FakeModel()
    run(session, private, third)
    assert third.calls == []      # answers are not


def test_a_reviewer_can_blur_more_or_box_by_hand(session: Path, private: Path) -> None:
    s = run(session, private, FakeModel(), blur=["f0", "f1:0.1,0.1,0.2,0.2"])
    by = {r.frame_id: r for r in s.frames}
    assert by["f0"].whole
    assert not by["f1"].whole and len(by["f1"].boxes) == 1   # the plate placed by hand
    assert run(session, private, FakeModel(), blur=["f2:0,0,0.1,0.1"]).frames[2].whole  # identifiers stay whole


def test_approval_is_for_exactly_what_was_reviewed(session: Path, private: Path, tmp_path: Path) -> None:
    s = run(session, private, FakeModel())
    into = tmp_path / "replays"
    with pytest.raises(SystemExit, match="review it again"):
        curate.approve("demo", "0" * 16, private, into)
    (s.dir / "frames/f0.jpg").write_bytes(jpeg(40))  # someone swaps a frame after the review
    with pytest.raises(SystemExit, match="review it again"):
        curate.approve("demo", s.digest, private, into)
    s = run(session, private, FakeModel())
    dest = curate.approve("demo", s.digest, private, into)
    assert sorted(p.name for p in dest.iterdir()) == ["events.jsonl", "frames", "meta.json"]
    assert curate.digest_of(dest) == s.digest


def test_the_staged_copy_plays_on_the_replay_host(session: Path, private: Path, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from deploy import replay_host

    s = run(session, private, FakeModel())
    into = tmp_path / "replays"
    curate.approve("demo", s.digest, private, into)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    monkeypatch.delenv("FIXIT_CALLS", raising=False)
    c = TestClient(replay_host.build(into, dist))
    r = c.get("/api/v1/replay/demo")
    assert r.status_code == 200 and TOKEN not in r.text
    assert c.get("/api/v1/replay/demo/frames/f0.jpg").status_code == 200


def test_bad_names_are_refused(session: Path, private: Path) -> None:
    import asyncio
    with pytest.raises(SystemExit):
        asyncio.run(curate.stage(session, "../x", private, FakeModel()))


def test_a_box_it_cannot_trust_blurs_the_whole_frame() -> None:
    # a model asked for fractions answered in pixels: that box would blur nothing, so the frame goes whole
    pixels = {"faces": [{"what": "face", "box": {"x": 375, "y": 310, "w": 230, "h": 210}}]}
    flat = {"faces": [{"what": "face", "box": {"x": 0.5, "y": 0.5, "w": 0.0, "h": 0.3}}]}
    for found in (pixels, flat):
        rv = curate.review_frame("f", {"identifiers": [], "plates": [], "brands": [], **found}, None)
        assert rv.whole and not rv.boxes and "whole frame" in rv.flags[0]
    ok = curate.review_frame("f", {"faces": [{"what": "face", "box": {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}}],
                                   "identifiers": [], "plates": [], "brands": []}, None)
    assert not ok.whole and ok.boxes[0].w == pytest.approx(0.1)  # clipped to the frame, still blurred


def test_approve_golden_writes_the_one_line(session: Path, private: Path, tmp_path: Path) -> None:
    s = run(session, private, FakeModel())
    into = tmp_path / "replays"
    curate.approve("demo", s.digest, private, into, make_golden=True)
    assert (into / "GOLDEN").read_text() == "demo\n"


def test_a_re_curate_replays_the_sealed_checks_after_the_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import calls
    from calls.testing import responses_events, write_respond_fixture

    private = tmp_path / "private"
    sealed = private / "fixtures-sealed-1027"
    sealed.mkdir(parents=True)
    (private / "fixtures-sealed-notes.txt").write_text("not a folder")
    monkeypatch.setenv("FIXIT_CALLS", "replay")
    client = curate._client(private)
    buf = io.BytesIO()
    Image.new("RGB", (32, 18), (90, 90, 90)).save(buf, "JPEG")
    frame = buf.getvalue()  # a 27 Oct frame
    body = client.build_respond_body("judge", curate.CHECK_PROMPT, images=[frame], format=curate.CHECK_FORMAT)
    write_respond_fixture(sealed, body, responses_events(text='{"faces": []}', model=body["model"]))
    assert not (private / "fixtures").exists()  # the sweep took the originals: only the sealed copy answers
    r = asyncio.run(client.respond("judge", curate.CHECK_PROMPT, images=[frame], format=curate.CHECK_FORMAT,
                                   lane=curate.LANE))
    assert r.parsed() == {"faces": []}

    seen: dict[str, Any] = {}
    monkeypatch.setattr(calls, "make_client", lambda mode, **kw: seen.update(kw))
    curate._client(private)
    assert seen["fixtures_root"] == private / "fixtures"  # a new check is recorded in fixtures/, never the seal
    assert seen["fixture_roots"] == [private / "fixtures", sealed]
