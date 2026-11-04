import json
from pathlib import Path

import pytest

from eval.label.store import LabelError, save_label, session_frames, state

PLAN_LINE = {"clip_id": "dw.filter_out.done.t1", "step_id": "dw.filter_out", "appliance": "dishwasher",
             "truth": "done", "take": 1, "shows": "x", "cant_see_kind": None, "guarded": False}


@pytest.fixture()
def priv(tmp_path: Path) -> Path:
    d = tmp_path / "private" / "sessions" / "cap1"
    (d / "frames").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"session_id": "cap1", "mode": "capture", "started_at": "t"}))
    events = []
    for i in range(6):
        (d / "frames" / f"f{i}.jpg").write_bytes(b"\xff\xd8 frame %d" % i)
        events.append({"t_ms": i * 500, "dir": "c2s", "kind": "frame", "msg": {"frame_id": f"f{i}", "t_ms": i * 500}})
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    other = tmp_path / "private" / "sessions" / "live1"
    other.mkdir()
    (other / "meta.json").write_text(json.dumps({"session_id": "live1", "mode": "api"}))
    (tmp_path / "plan.jsonl").write_text(json.dumps(PLAN_LINE) + "\n")
    return tmp_path / "private"


def _form(**kw: object) -> dict[str, object]:
    return {"clip_id": "dw.filter_out.done.t1", "session_id": "cap1", "truth": "done", "consent": "c1",
            "scanned": True, "before": "f0", "start": "f3", "end": "f5", **kw}


def _save(priv: Path, **kw: object) -> dict[str, object]:
    root = priv.parent
    return save_label(priv, _form(**kw), plan_path=root / "plan.jsonl", labels_path=root / "labels.jsonl")


def test_only_capture_sessions_listed_and_frames_in_order(priv: Path) -> None:
    st = state(priv, priv.parent / "plan.jsonl", priv.parent / "labels.jsonl")
    assert [s["session_id"] for s in st["sessions"]] == ["cap1"]
    assert [f.frame_id for f in session_frames(priv, "cap1")] == [f"f{i}" for i in range(6)]


def test_save_copies_clip_and_writes_manifest(priv: Path) -> None:
    line = _save(priv)
    frames = line["frames"]
    assert isinstance(frames, list) and [f["role"] for f in frames] == ["before", "now", "now", "now"]
    assert all(len(f["sha256"]) == 64 for f in frames)
    assert (priv / "clips-raw" / "dw.filter_out.done.t1" / "frames" / "000_before.jpg").exists()
    labels = (priv.parent / "labels.jsonl").read_text().splitlines()
    assert len(labels) == 1
    _save(priv, note="again")  # upsert, not append
    assert len((priv.parent / "labels.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize(("kw", "match"), [
    ({"truth": "not_done"}, "give the reason"),
    ({"consent": ""}, "consent"),
    ({"scanned": False}, "scan"),
    ({"session_id": "../etc"}, "bad session id"),
    ({"clip_id": "nope"}, "not in the plan"),
    ({"before": "f4", "start": "f3"}, "BEFORE <= window start"),
])
def test_refusals(priv: Path, kw: dict[str, object], match: str) -> None:
    with pytest.raises(LabelError, match=match):
        _save(priv, **kw)


def test_relabel_with_reason_is_kept(priv: Path) -> None:
    line = _save(priv, truth="not_done", relabel_reason="lug did not click")
    assert line["planned_truth"] == "done" and line["relabel_reason"] == "lug did not click"


def test_reads_l1_recorder_capture_layout(tmp_path: Path) -> None:
    """The tool reads what the app's Recorder writes in capture mode, not just our hand-made fixture."""
    from datetime import UTC, datetime

    from app.recorder import Recorder
    from contracts.models.session import SessionMeta

    meta = SessionMeta(session_id="capL1", started_at=datetime(2026, 10, 29, 10, tzinfo=UTC), mode="capture",
                       lang="en", contracts_version="1.3.0")
    rec = Recorder(tmp_path / "sessions", meta)
    for i in (2, 0, 1):  # arrival order is not name order
        rec.client(i * 400.0, "c2s", {"type": "frame", "frame_id": f"x{i}", "t_ms": i * 400, "jpeg_1024": "b64"})
        rec.frame(f"x{i}", b"\xff\xd8 %d" % i)
    st = state(tmp_path, tmp_path / "none.jsonl", tmp_path / "none2.jsonl")
    assert [s["session_id"] for s in st["sessions"]] == ["capL1"]
    assert [(f.frame_id, f.t_ms) for f in session_frames(tmp_path, "capL1")] == [("x0", 0), ("x1", 400), ("x2", 800)]
