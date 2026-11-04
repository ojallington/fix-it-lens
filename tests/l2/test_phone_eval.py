"""The phone's eval rows: trials scored from a session record by their windows, the phone's own gap at a stop, and
claims that stay unmeasured (n=0, no value) until there are labels."""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import pytest

from eval import phone
from eval.rows import load_rows


def record(tmp: Path, sid: str, lines: list[dict[str, Any]]) -> Path:
    d = tmp / "sessions" / sid
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    return tmp / "sessions"


def zone(
    zid: str, ring: float, tool: float | None = None, frame: str = "f0", rule: str = "nl.gas.tap"
) -> dict[str, Any]:
    m: dict[str, Any] = {
        "type": "zone",
        "zone_id": zid,
        "rule_id": rule,
        "ring_cm": ring,
        "mask_svg": "",
        "frame_id": frame,
    }
    if tool is not None:
        m["tool_len_cm"] = tool
    return {"t_ms": 0, "dir": "s2c", "kind": "zone", "msg": m}


def g1(t: int, frame: str, zid: str = "z1", kind: str = "stop", dist: float | None = 2.0) -> dict[str, Any]:
    m = {
        "type": "g1",
        "seq": t,
        "t_ms": t,
        "zone_id": zid,
        "kind": kind,
        "ttc_ms": 300,
        "dist_cm": dist,
        "frame_id": frame,
        "reason": "reach",
    }
    return {"t_ms": t, "dir": "c2s", "kind": "g1", "msg": m}


def test_gap_adds_the_ring_never_under_three_and_the_tool() -> None:
    assert phone.phone_gap_cm({"dist_cm": 2.5}, {"ring_cm": 5}) == 7.5
    assert phone.phone_gap_cm({"dist_cm": 2.5}, {"ring_cm": 1}) == 5.5
    assert phone.phone_gap_cm({"dist_cm": 1}, {"ring_cm": 4, "tool_len_cm": 12}) == 17.0
    assert phone.phone_gap_cm({"dist_cm": None}, {"ring_cm": 4}) == 4.0


def test_trials_count_a_stop_on_their_part_inside_their_window_only(tmp_path: Path) -> None:
    sessions = record(
        tmp_path,
        "s1",
        [
            zone("z1", 4),
            zone("z2", 3, rule="nl.mains.casing"),
            g1(1000, "a"),
            g1(5000, "b", kind="pause"),
            g1(9000, "c"),
            g1(2500, "d", zid="z2"),
            g1(12000, "e", zid="z2"),
        ],
    )
    trials = [
        {"trial_id": "r1", "kind": "reach", "session_id": "s1", "from_ms": 500, "to_ms": 1500},
        {"trial_id": "n1", "kind": "near_pass", "session_id": "s1", "from_ms": 4000, "to_ms": 6000},
        {"trial_id": "r2", "kind": "reach", "session_id": "s1", "from_ms": 8000, "to_ms": 9000},
        {"trial_id": "c1", "kind": "casing_reach", "session_id": "s1", "from_ms": 2000, "to_ms": 3000},
        # A casing stop in a gas trial's window is not a gas stop, nor a gas stop in a casing trial's.
        {"trial_id": "r3", "kind": "reach", "session_id": "s1", "from_ms": 11000, "to_ms": 13000},
        {"trial_id": "c2", "kind": "casing_reach", "session_id": "s1", "from_ms": 800, "to_ms": 1200},
    ]
    read = phone.read_trials(trials, sessions)
    assert [(r["trial_id"], r["stopped"], r["frame_id"]) for r in read] == [
        ("r1", True, "a"),
        ("n1", False, None),
        ("r2", True, "c"),
        ("c1", True, "d"),
        ("r3", False, None),
        ("c2", False, None),
    ]
    assert read[0]["after_ms"] == 500.0
    claims = {c["id"]: c for c in phone.stop_claims(read)}
    assert (claims["phone.gas.reach_stopped"]["value"], claims["phone.gas.reach_stopped"]["n"]) == (2, 3)
    assert (claims["phone.gas.near_pass_false_stops"]["value"], claims["phone.gas.near_pass_false_stops"]["n"]) == (
        0,
        1,
    )
    assert claims["phone.casing.reach_stopped"]["ci"] is not None
    assert claims["phone.gas.covers_stopped"] == {
        **claims["phone.gas.covers_stopped"],
        "n": 0,
        "value": None,
        "ci": None,
    }


def test_a_trial_of_another_kind_or_without_a_record_fails(tmp_path: Path) -> None:
    sessions = record(tmp_path, "s1", [])
    with pytest.raises(ValueError):
        phone.read_trials([{"trial_id": "x", "kind": "wave", "session_id": "s1", "from_ms": 0, "to_ms": 1}], sessions)
    with pytest.raises(FileNotFoundError):
        phone.read_trials(
            [{"trial_id": "x", "kind": "reach", "session_id": "nope", "from_ms": 0, "to_ms": 1}], sessions
        )


def test_gap_fill_uses_the_latest_zone_before_the_stop(tmp_path: Path) -> None:
    sessions = record(
        tmp_path, "s1", [zone("z1", 4), g1(100, "a"), zone("z1", 6, tool=10), g1(200, "b"), g1(300, "c", zid="z9")]
    )
    stops = [
        {"stop_id": "g1", "kind": "gas", "session_id": "s1", "frame_id": "a", "phone_gap_cm": None},
        {"stop_id": "g2", "kind": "gas", "session_id": "s1", "frame_id": "b"},
        {"stop_id": "g3", "kind": "gas", "session_id": "s1", "frame_id": "c"},
        {"stop_id": "g4", "kind": "gas", "phone_gap_cm": 3.1},
    ]
    out, missing = phone.fill_gap(stops, sessions)
    assert [s.get("phone_gap_cm") for s in out] == [6.0, 18.0, None, 3.1]
    assert missing == ["g3"]
    # Each line keeps its own key order, so a fill shows as the phone_gap_cm change alone.
    path = tmp_path / "gap.jsonl"
    phone.write_jsonl(path, out)
    assert list(json.loads(path.read_text().splitlines()[1])) == [
        "stop_id",
        "kind",
        "session_id",
        "frame_id",
        "phone_gap_cm",
    ]


def test_stop_ms_is_the_p95_of_the_stops_that_ended_a_trial(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    frames = [{"frame_id": str(i), "ms": float(i)} for i in range(1, 101)] + [{"frame_id": "setup", "ms": 900.0}]
    (runs / "s1.json").write_text(
        json.dumps({"session_id": "s1", "at": "x", "user_agent": "ua", "stop_frames": frames})
    )
    timings = phone.read_timings(runs)
    assert set(timings[0]) == {"session_id", "at", "stops"}
    readings = [
        {"trial_id": f"r{i}", "kind": "reach", "session_id": "s1", "stopped": True, "frame_id": str(i)}
        for i in range(1, 101)
    ]
    readings += [
        {"trial_id": "n1", "kind": "near_pass", "session_id": "s2", "stopped": True, "frame_id": "setup"},
        {"trial_id": "n2", "kind": "near_pass", "session_id": "s1", "stopped": False, "frame_id": None},
    ]
    (c,) = phone.stop_ms_claims(timings, readings)
    assert c["id"] == "phone.stop_p95_ms" and c["n"] == 100 and 94 <= c["value"] <= 96
    assert phone.stop_ms_claims(timings, [])[0] == {**c, "value": None, "n": 0}


def test_readings_from_other_labels_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trials, stops = tmp_path / "trials.jsonl", tmp_path / "stops.jsonl"
    monkeypatch.setattr(phone, "TRIALS", trials)
    monkeypatch.setattr(phone, "STOPS_READ", stops)
    monkeypatch.setattr(phone, "TIMINGS_READ", tmp_path / "none.jsonl")
    monkeypatch.setattr(phone, "EVAL", tmp_path / "eval")
    phone.write_jsonl(trials, [{"trial_id": "r1", "kind": "reach", "session_id": "s1", "from_ms": 0, "to_ms": 1}])
    phone.write_jsonl(
        stops, [{"trial_id": "r1", "kind": "reach", "session_id": "s1", "stopped": True, "frame_id": "a"}]
    )
    assert phone.main(["stops"]) == 0
    capsys.readouterr()
    phone.write_jsonl(trials, [{"trial_id": "r1", "kind": "near_pass", "session_id": "s1", "from_ms": 0, "to_ms": 1}])
    for what in ("stops", "stop-ms"):
        assert phone.main([what]) == 1
        cap = capsys.readouterr()
        assert cap.out == "" and "run eval.phone read" in cap.err


def test_the_rows_print_exactly_their_claims_unmeasured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(phone, "STOPS_READ", tmp_path / "none.jsonl")
    monkeypatch.setattr(phone, "TIMINGS_READ", tmp_path / "none.jsonl")
    monkeypatch.setattr(phone, "TRIALS", tmp_path / "none.jsonl")
    printed: dict[str, list[str]] = {}
    for what in ("stops", "stop-ms"):
        assert phone.main([what]) == 0
        out = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
        assert all(c["n"] == 0 and c["value"] is None for c in out)
        printed[what] = sorted(c["id"] for c in out)
    assert printed["stops"] == sorted(cid for cid, _, _ in phone.STOP_CLAIMS)
    rows = {r.command: r for r in load_rows() if r.owner == "l2"}
    for what, ids in printed.items():
        row = rows.get(f"python -m eval.phone {what}")
        if row is not None:
            # A row lists ids or families ("phone.gas.*"): each printed id is in one, and each one prints something.
            assert all(any(fnmatchcase(i, p) for p in row.claims) for i in ids)
            assert all(any(fnmatchcase(i, p) for i in ids) for p in row.claims)
            assert row.unmeasured and not any(ch.isdigit() for ch in row.unmeasured)
