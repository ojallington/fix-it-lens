import json
from pathlib import Path

import pytest

from eval.anyappliance import claims, read, roster_models

ROSTER = [{"record": "roster", "appliance_id": f"a{i}", "maker": "m", "model": f"M{i}", "build": True}
          for i in range(1, 4)] + [{"record": "roster", "appliance_id": "a9", "maker": "m", "model": "M9",
                                    "build": False}]


def run(i: int, **kw: object) -> dict[str, object]:
    return {"record": "run", "run": "baseline", "appliance_id": f"a{i}", "build_ok": True, "checked_by_code": True,
            "verdict": "usable", "verdict_by": "builder", "classes": [], "seconds": 100.0 + i, **kw}


def test_baseline_counts_carry_count_cis_and_the_rerun_is_pending() -> None:
    rows = ROSTER + [run(1), run(2, verdict="wrong", classes=[1, 4]), run(3, build_ok=False, checked_by_code=False,
                                                                          verdict=None)]
    c = {x["id"]: x for x in claims(rows)}
    assert (c["anyappliance.baseline.usable"]["value"], c["anyappliance.baseline.usable"]["n"]) == (1, 3)
    assert "builder" in c["anyappliance.baseline.usable"]["unit"] and "not independent" in c[
        "anyappliance.baseline.usable"]["note"]
    assert c["anyappliance.baseline.checked_by_code"]["value"] == 2 and c["anyappliance.baseline.class4"]["value"] == 1
    lo, hi = c["anyappliance.baseline.build_ok"]["ci"]
    assert lo <= 2 <= hi <= 3
    # a build that made no card has no "manual to card" time
    assert (c["anyappliance.baseline.seconds_median"]["value"], c["anyappliance.baseline.seconds_median"]["n"]) == (
        101.5, 2)
    assert "wall-clock" in c["anyappliance.baseline.seconds_median"]["note"]
    assert c["anyappliance.rerun.usable"]["n"] == 0


def test_each_run_states_how_its_seconds_were_timed() -> None:
    rerun = [run(i, run="rerun", seconds_basis="model") for i in (1, 2, 3)]
    c = {x["id"]: x for x in claims(ROSTER + [run(1), run(2), run(3)] + rerun)}
    assert "model calls" in c["anyappliance.rerun.seconds_median"]["unit"]
    assert "model calls" not in c["anyappliance.baseline.seconds_median"]["unit"]
    with pytest.raises(ValueError, match="seconds_basis"):
        claims(ROSTER + [run(1), run(2, seconds_basis="model"), run(3)])


def test_a_run_must_cover_the_whole_roster() -> None:
    with pytest.raises(ValueError, match="roster"):
        claims(ROSTER + [run(1), run(2)])


def test_the_committed_rows_hold_no_manual_text_and_cover_the_roster() -> None:
    rows = read()
    assert len(roster_models(rows)) == 12 and claims(rows)
    allowed = {"record", "run", "appliance_id", "maker", "kind", "model", "lang", "build", "why", "pages",
               "manual_from", "lookup", "build_ok", "jobs", "jobs_shown", "jobs_refused_g0", "steps",
               "quotes_dropped", "checked_by_code", "verdict", "verdict_by", "classes", "seconds", "usd",
               "seconds_basis", "run_tag"}
    assert all(set(r) <= allowed for r in rows), json.dumps([sorted(set(r) - allowed) for r in rows])
    assert Path("eval/labels/anyappliance.jsonl").stat().st_size < 20_000
