import asyncio
import json
from pathlib import Path

import pytest

from eval import hazard
from eval.hazard import Line, e2e_claims, lines, server_claims, server_run
from eval.label import readoff
from eval.label.store import LabelError

SPOKEN_ZONES = {"smell", "alarm", "fire", "burning", "flood", "breaker_repeat"}


def test_the_script_is_20_spoken_lines_in_both_languages() -> None:
    script = lines()
    assert len(script) == 20 and len({x.id for x in script}) == 20
    assert sum(x.lang == "en" for x in script) == sum(x.lang == "nl" for x in script) == 10
    assert {x.zone for x in script} <= SPOKEN_ZONES


def test_every_line_stops_a_session_under_its_own_hazard() -> None:
    results = asyncio.run(server_run(lines()))
    assert [x.id for x, ms in results if ms is None] == []
    [c] = server_claims(results, latency=False)
    assert (c["value"], c["n"]) == (20, 20)


def test_a_line_that_stops_under_another_rule_is_a_miss() -> None:
    [(_, ms)] = asyncio.run(server_run([Line("x1", "en", "flood", "I smell gas.")]))
    assert ms is None
    [(_, ms)] = asyncio.run(server_run([Line("x2", "en", "smell", "The filter looks clean.")]))
    assert ms is None


def _r(line: str, end: float, stop: float | None, clip: str = "c1") -> dict[str, object]:
    return {"clip_id": clip, "sha256": "0" * 64, "line_id": line, "speech_end_ms": end, "stop_shown_ms": stop,
            "fps": 60, "phone": "Pixel 8", "network": "home wifi"}


def test_e2e_reads_latency_off_the_readings() -> None:
    script = lines()
    rs = [_r("h01", 1000, 1400), _r("h02", 2000, 2650), _r("h03", 500, None), _r("h01", 1000, 1300)]
    c = {x["id"]: x for x in e2e_claims(rs, script)}
    assert (c["hazard.e2e.stopped"]["value"], c["hazard.e2e.stopped"]["n"]) == (2, 3)  # h01 read twice counts once
    assert (c["hazard.e2e.p95_ms"]["value"], c["hazard.e2e.max_ms"]["value"], c["hazard.e2e.p95_ms"]["n"]) == (
        650, 650, 2)
    assert "Pixel 8, home wifi, 60 fps" in c["hazard.e2e.p95_ms"]["note"]
    with pytest.raises(ValueError, match="not in eval/hazard.toml"):
        e2e_claims([_r("zz", 0, 1)], script)


def test_e2e_before_the_shoot_is_unmeasured() -> None:
    assert all(c["n"] == 0 for c in e2e_claims([], lines()))


def test_the_sheet_lists_every_line_and_stages_nothing() -> None:
    s = hazard.sheet(lines())
    assert all(x.text in s for x in lines())
    assert "60 fps" in s and "with sound" in s and "Nothing is staged" in s and "gas tap is never touched" in s


def test_a_reading_is_saved_with_the_clips_hash_one_per_line(tmp_path: Path) -> None:
    clips = tmp_path / "clips-raw" / "hazard"
    clips.mkdir(parents=True)
    (clips / "take1.mp4").write_bytes(b"not really a video")
    out = tmp_path / "readings.jsonl"
    form = {"clip_id": "take1", "line_id": "h02", "speech_end_ms": 1000.04, "stop_shown_ms": 1416.7, "fps": 60,
            "phone": "Pixel 8", "network": "home wifi"}
    r = readoff.save_reading(tmp_path, form, readings_path=out)
    assert r["sha256"] == readoff.sha256(clips / "take1.mp4") and r["speech_end_ms"] == 1000.0
    readoff.save_reading(tmp_path, {**form, "line_id": "h01"}, readings_path=out)
    readoff.save_reading(tmp_path, {**form, "stop_shown_ms": None}, readings_path=out)  # h02 again: replaces
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert [(x["line_id"], x["stop_shown_ms"]) for x in rows] == [("h01", 1416.7), ("h02", None)]
    for bad, why in (({"line_id": "zz"}, "not in"), ({"stop_shown_ms": 900}, "after"), ({"phone": ""}, "phone"),
                     ({"clip_id": "../x"}, "bad clip_id"), ({"clip_id": "nope"}, "no clip"),
                     ({"speech_end_ms": -1}, "time in ms"), ({"fps": 7}, "fps")):
        with pytest.raises(LabelError, match=why):
            readoff.save_reading(tmp_path, {**form, **bad}, readings_path=out)


def test_the_readoff_state_lists_clips_and_lines(tmp_path: Path) -> None:
    (tmp_path / "clips-raw" / "hazard").mkdir(parents=True)
    (tmp_path / "clips-raw" / "hazard" / "take1.MOV").write_bytes(b"x")
    (tmp_path / "clips-raw" / "hazard" / "notes.txt").write_text("x")
    st = readoff.state(tmp_path, readings_path=tmp_path / "none.jsonl")
    assert [c["clip_id"] for c in st["clips"]] == ["take1"] and len(st["lines"]) == 20 and st["readings"] == []
