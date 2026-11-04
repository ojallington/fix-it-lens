"""G1: rules/g1.json is current, valid, and the Python reference passes every vector (the phone's TypeScript runs the
same)."""

from __future__ import annotations

import json

import pytest

from contracts import validate
from contracts.models.g1 import G1Constants, G1Frame
from rules import g1_ref, g1_vectors

G1 = g1_ref.load()


def test_g1_json_is_current() -> None:
    assert g1_vectors.main(["--check"]) == 0


def test_g1_json_matches_schema() -> None:
    validate("g1", json.loads(g1_ref.G1_PATH.read_text()))


def test_at_least_30_vectors_covering_the_brief() -> None:
    ids = {v.id for v in G1.vectors}
    assert len(ids) == len(G1.vectors) >= 30
    for family in ("reach_slow", "reach_normal", "reach_fast", "ring_step", "tool", "covered", "hand_lost", "stale"):
        assert any(i.startswith(family) for i in ids), family


@pytest.mark.parametrize("vector", G1.vectors, ids=lambda v: v.id)
def test_vector(vector: object) -> None:
    from contracts.models.g1 import G1Vector

    assert isinstance(vector, G1Vector)
    event = g1_ref.run(vector.frames, G1.constants)
    got = (None, None) if event is None else (event.t_ms, event.kind)
    assert got == (vector.expect.stop_at_t_ms, vector.expect.kind)


def _event(vid: str) -> g1_ref.G1Event:
    v = next(v for v in G1.vectors if v.id == vid)
    e = g1_ref.run(v.frames, G1.constants)
    assert e is not None
    return e


def test_fast_reach_stops_further_out_than_slow() -> None:
    assert _event("reach_fast_left").gap_cm > _event("reach_slow_left").gap_cm  # type: ignore[operator]


def test_one_metre_a_second_leaves_12cm_after_the_stop_path_budget() -> None:
    # The design: with the stop path's p95 of 150 ms the screen is red with the hand still >= 12 cm away, up to 1 m/s.
    for d in ("left", "above", "diagonal"):
        gap = _event(f"reach_fast_{d}").gap_cm
        assert gap is not None and gap - 0.1 * G1.constants.stop_p95_ms >= 12


def test_ring_step_reads_under_12cm_on_a_slow_reach() -> None:
    e = _event("ring_step_reach_slow")
    assert e.reason == "ttc" and e.gap_cm is not None and e.gap_cm < 12


def test_ring_is_clamped_to_the_floor_in_code() -> None:
    # The schema rejects ring_cm < 3; code clamps again in case a model was built without validation.
    frame = G1Frame.model_validate({"t_ms": 0, "fingertips": [{"x": 0, "y": 0}], "tool_len_cm": 0, "zone": {
        "polygon": [{"x": 100, "y": 0}, {"x": 200, "y": 0}, {"x": 200, "y": 100}], "ring_cm": 3,
        "scale_cm_per_px": 0.1}})
    frame.zone.ring_cm = 1  # bypass validation
    assert g1_ref.gap_cm(frame.fingertips[0], frame, G1.constants) == pytest.approx(10 - 3)


def test_floor_below_3_fails_to_load(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    data = json.loads(g1_ref.G1_PATH.read_text())
    data["constants"]["ring_floor_cm"] = 2
    p = tmp_path / "g1.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        g1_ref.load(p)


def test_constants_are_the_design_values() -> None:
    assert G1.constants == G1Constants(ttc_ms=400, dist_cm=20, ring_floor_cm=3, frame_max_age_ms=200, stop_p95_ms=150,
                                      near_cm=40)
