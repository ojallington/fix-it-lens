"""Builds `rules/g1.json`: the G1 constants and the shared vectors that the phone (TypeScript) and `g1_ref.py` pass.

    uv run python -m rules.g1_vectors          # rewrite rules/g1.json
    uv run python -m rules.g1_vectors --check  # fail if it is out of date

Each vector's expectation is worked out here from the geometry (a straight reach has a known gap per frame and a
known closing speed), not by running the reference, so the reference is tested against something independent.

The scene: a 10 x 10 cm zone (pixels 400..500 x 300..400) at 0.1 cm per pixel, so 1 px/ms is 1 m/s. Frames come
every 33 ms (30 fps).
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

from rules.g1_ref import G1_PATH

VERSION = "1.0.0"
CONSTANTS = {"ttc_ms": 400, "dist_cm": 20, "ring_floor_cm": 3, "frame_max_age_ms": 200, "stop_p95_ms": 150,
             "near_cm": 40}
DT = 33
SCALE = 0.1
LEFT, RIGHT, TOP, BOTTOM = 400.0, 500.0, 300.0, 400.0
SQUARE = [{"x": LEFT, "y": TOP}, {"x": RIGHT, "y": TOP}, {"x": RIGHT, "y": BOTTOM}, {"x": LEFT, "y": BOTTOM}]

Vec = dict[str, Any]
Pt = tuple[float, float]


def zone(ring_cm: float = 3, ring_step: bool = False) -> dict[str, Any]:
    z: dict[str, Any] = {"polygon": SQUARE, "ring_cm": ring_cm, "scale_cm_per_px": SCALE}
    if ring_step:
        z["ring_step"] = True
    return z


def frame(t: float, tips: list[Pt], z: dict[str, Any], tool: float = 0, age: float = 0) -> dict[str, Any]:
    f: dict[str, Any] = {
        "t_ms": t,
        "fingertips": [{"x": round(x, 3), "y": round(y, 3)} for x, y in tips],
        "zone": z,
        "tool_len_cm": tool,
    }
    if age:
        f["frame_age_ms"] = age
    return f


def vector(vid: str, description: str, frames: list[dict[str, Any]], stop_at: float | None, kind: str | None) -> Vec:
    return {"id": vid, "description": description, "frames": frames, "expect": {"stop_at_t_ms": stop_at, "kind": kind}}


def edge_px(p: Pt) -> float:
    """Distance from a point outside the square to its edge."""
    dx = max(LEFT - p[0], 0.0, p[0] - RIGHT)
    dy = max(TOP - p[1], 0.0, p[1] - BOTTOM)
    return math.hypot(dx, dy)


def reach_expect(path: list[Pt], ring: float, tool: float, ring_step: bool) -> float | None:
    """The oracle for one fingertip on a path: the first t at which G1 must stop."""
    dist = CONSTANTS["dist_cm"]
    prev: float | None = None
    for k, p in enumerate(path):
        t = k * DT
        inside = LEFT < p[0] < RIGHT and TOP < p[1] < BOTTOM
        gap = max(0.0, edge_px(p) * SCALE - ring - tool)
        if inside or gap == 0 or (not ring_step and gap < dist):
            return t
        if prev is not None:
            closing = (prev - gap) / DT
            if closing > 0 and gap / closing < CONSTANTS["ttc_ms"]:
                return t
        prev = gap
    return None


def line(start: Pt, direction: Pt, speed_m_s: float, n: int) -> list[Pt]:
    norm = math.hypot(*direction)
    step = speed_m_s * DT  # px per frame
    return [(start[0] + direction[0] / norm * step * k, start[1] + direction[1] / norm * step * k) for k in range(n)]


def reach(vid: str, desc: str, start: Pt, direction: Pt, speed: float, n: int = 60, ring: float = 3, tool: float = 0,
          ring_step: bool = False) -> Vec:
    path = line(start, direction, speed, n)
    stop = reach_expect(path, ring, tool, ring_step)
    assert stop is not None, vid
    # The frames end at the stop, so the vector also says nothing should happen before it.
    path = path[: int(stop // DT) + 1]
    z = zone(ring, ring_step)
    return vector(vid, desc, [frame(k * DT, [p], z, tool) for k, p in enumerate(path)], stop, "stop")


def path_vec(vid: str, desc: str, path: list[Pt], ring: float = 3, tool: float = 0, ring_step: bool = False) -> Vec:
    stop = reach_expect(path, ring, tool, ring_step)
    if stop is not None:
        path = path[: int(stop // DT) + 1]
    z = zone(ring, ring_step)
    return vector(vid, desc, [frame(k * DT, [p], z, tool) for k, p in enumerate(path)], stop,
                  None if stop is None else "stop")


def still(vid: str, desc: str, p: Pt, n: int = 10, ring: float = 3, tool: float = 0, ring_step: bool = False) -> Vec:
    stop = reach_expect([p] * n, ring, tool, ring_step)
    z = zone(ring, ring_step)
    frames = [frame(k * DT, [p], z, tool) for k in range(n if stop is None else int(stop // DT) + 1)]
    return vector(vid, desc, frames, stop, None if stop is None else "stop")


def build() -> dict[str, Any]:
    v: list[Vec] = []
    # Straight reaches at 0.2, 0.5 and 1.0 m/s from three directions. A fast reach stops further out (TTC) than a
    # slow one (distance).
    speeds = {"slow": 0.2, "normal": 0.5, "fast": 1.0}
    starts: dict[str, tuple[Pt, Pt]] = {
        "left": ((-353.7, 350.0), (1, 0)),
        "above": ((450.0, -411.3), (0, 1)),
        "diagonal": ((-160.9, -180.4), (1, 0.9)),
    }
    for sname, speed in speeds.items():
        for dname, (start, direction) in starts.items():
            v.append(reach(f"reach_{sname}_{dname}", f"A {speed} m/s reach from the {dname} stops before contact",
                           start, direction, speed, n=200))
    # 1.5 m/s, faster than the design speed: still stops, earlier still.
    v.append(reach("reach_very_fast_left", "A 1.5 m/s reach stops on TTC well outside 20 cm",
                   (-753.7, 350.0), (1, 0), 1.5, n=200))
    # A zone with a wider ring (the casing's 5 cm) stops further out.
    v.append(reach("reach_normal_ring5", "A 5 cm ring moves the stop out by 2 cm", (-353.7, 350.0), (1, 0), 0.5,
                   n=200, ring=5))

    # Ring steps: the distance trigger gives way; TTC runs against the 3 cm ring alone.
    for sname, speed in speeds.items():
        v.append(reach(f"ring_step_reach_{sname}", f"Ring step: a {speed} m/s reach stops on TTC against the ring",
                       (-153.7, 350.0), (1, 0), speed, n=200, ring_step=True))
    # The fill valve sits 6 cm from the tap (3 cm outside the ring); the hand works it, moving across, not towards.
    fill = [(340.0 + 4 * math.sin(k / 3), 330.0 + 30 * math.sin(k / 5)) for k in range(30)]
    v.append(path_vec("ring_step_fill_valve_ops", "Ring step: working the fill valve 6 cm from the tap never stops",
                      fill, ring_step=True))
    v.append(still("ring_step_still_near", "Ring step: a still hand 4 cm out never stops", (360.0, 350.0),
                   ring_step=True))
    v.append(still("no_ring_step_same_place", "Off a ring step, the same hand 4 cm out stops on distance at once",
                   (360.0, 350.0)))

    # Near passes: across the zone's face, parallel to its edge.
    v.append(path_vec("near_pass_25cm", "A 1 m/s pass 25 cm out, alongside the zone, never stops",
                      [(120.0, 300.0 + 33.0 * k) for k in range(4)]))
    v.append(path_vec("near_pass_15cm", "A pass 15 cm out stops on distance",
                      [(220.0, 300.0 + 33.0 * k) for k in range(4)]))

    # Still hands.
    v.append(still("still_hand_far", "A still hand 50 cm away never stops", (-130.0, 350.0)))
    v.append(still("still_hand_21cm", "A still hand 21 cm out (24 cm to the edge, less the ring) never stops",
                   (160.0, 350.0)))
    v.append(still("still_hand_19cm", "A still hand 19 cm out stops on distance at once", (180.0, 350.0)))

    # A tool in the hand counts as part of the hand.
    v.append(reach("tool_reach_normal", "A 10 cm screwdriver: the 0.5 m/s reach stops 10 cm further out",
                   (-353.7, 350.0), (1, 0), 0.5, n=200, tool=10))
    v.append(still("tool_still_25cm", "A hand 28 cm out holding a 10 cm tool stops on distance", (120.0, 350.0),
                   tool=10))
    v.append(still("no_tool_still_25cm", "The same hand with no tool never stops", (120.0, 350.0)))

    # Covering: a fingertip over the outline counts as touching.
    z = zone()
    v.append(vector("covered_jump", "The tracker jumps from far away into the outline: stop on that frame",
                    [frame(0, [(-100.0, 350.0)], z), frame(33, [(-90.0, 350.0)], z), frame(66, [(450.0, 350.0)], z)],
                    66, "stop"))
    v.append(vector("covered_one_of_five", "One fingertip of five inside the outline stops",
                    [frame(0, [(450.0, 350.0), (-100, 300), (-110, 310), (-120, 320), (-130, 330)], z)], 0, "stop"))
    v.append(vector("nearest_of_two_hands", "Two hands, one far and one 15 cm out: the near one stops",
                    [frame(0, [(-300.0, 350.0), (230.0, 350.0)], z)], 0, "stop"))
    v.append(vector("contact_ring_edge", "A fingertip on the ring's outer edge (gap 0) stops",
                    [frame(0, [(530.0, 350.0)], zone(3, ring_step=True))], 0, "stop"))

    # Fails closed: the lost hand and the stale frame.
    v.append(vector("hand_lost", "Losing the hand 30 cm from the zone pauses",
                    [frame(0, [(70.0, 350.0)], z), frame(33, [], z)], 33, "pause"))
    v.append(vector("hand_lost_39cm", "Losing the hand 39 cm out (under near, 40) pauses",
                    [frame(0, [(-20.0, 350.0)], z), frame(33, [], z)], 33, "pause"))
    v.append(vector("hand_lost_ring_step", "On a ring step, losing the hand 3 cm from the ring pauses",
                    [frame(0, [(340.0, 350.0)], zone(3, True)), frame(33, [], zone(3, True))], 33, "pause"))
    v.append(vector("hand_lost_far", "Losing the hand 60 cm away does not pause",
                    [frame(0, [(-230.0, 350.0)], z), frame(33, [], z), frame(66, [], z)], None, None))
    v.append(vector("hand_never_seen", "No hand at all is not a pause", [frame(t, [], z) for t in (0, 33, 66)],
                    None, None))
    v.append(vector("stale_frame_far", "A frame 250 ms old pauses even with the hand far away",
                    [frame(0, [(-230.0, 350.0)], z), frame(33, [(-230.0, 350.0)], z, age=250)], 33, "pause"))
    v.append(vector("stale_frame_at_limit", "A frame exactly 200 ms old is not stale (strictly older pauses)",
                    [frame(0, [(-230.0, 350.0)], z, age=200), frame(33, [(-230.0, 350.0)], z, age=200)], None, None))
    v.append(vector("stale_beats_covered", "A stale frame pauses before any stop is judged on it",
                    [frame(0, [(450.0, 350.0)], z, age=500)], 0, "pause"))

    # TTC edges.
    retreat = [(170.0 - 33.0 * k, 350.0) for k in range(10)]
    v.append(path_vec("retreat_fast", "A fast retreat starting 20 cm out never stops on TTC", retreat))
    v.append(vector("ttc_needs_same_count", "A second fingertip appearing resets TTC for that frame",
                    [frame(0, [(-40.0, 350.0)], z), frame(33, [(-7.0, 350.0), (-600.0, 0.0)], z),
                     frame(66, [(26.0, 350.0), (-600.0, 0.0)], z)], 66, "stop"))
    v.append(vector("ttc_needs_time", "Two frames with the same timestamp give no TTC",
                    [frame(0, [(-40.0, 350.0)], z), frame(0, [(-7.0, 350.0)], z)], None, None))

    return {"version": VERSION, "constants": CONSTANTS, "vectors": v}


def render() -> str:
    return json.dumps(build(), indent=1) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if G1_PATH.read_text() != text:
            print("rules/g1.json is out of date: run python -m rules.g1_vectors", file=sys.stderr)
            return 1
        return 0
    G1_PATH.write_text(text)
    print(f"wrote {G1_PATH} ({len(build()['vectors'])} vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
