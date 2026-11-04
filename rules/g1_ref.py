"""G1, the do-not-touch zone stop: the Python reference for `rules/g1.json`.

The phone runs G1 (phone/src/g1.ts); this file is its twin, and both must pass the same vectors. Per frame, in order,
the first event wins:

1. the frame is older than `frame_max_age_ms` -> pause (stale frame), whatever the hand is doing;
2. no fingertips, and the previous frame's nearest fingertip was within `near_cm` -> pause (hand lost);
3. a fingertip inside the zone's polygon -> stop (covered: a hand over the outline counts as touching it);
4. a fingertip's gap is 0 -> stop;
5. not a ring step, and a gap under `dist_cm` -> stop (distance);
6. a fingertip's time to contact under `ttc_ms` -> stop (ttc). TTC runs per fingertip index, only when the previous
   frame had the same number of fingertips, time moved on and the gap is closing.

gap_cm = max(0, distance to the polygon's edge x scale - ring_cm - tool_len_cm), with ring_cm never below the floor.
Comparisons are strict. There is no switch that turns any of this off.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from contracts.models.g1 import G1Constants, G1File, G1Frame, Point, Zone

G1_PATH = Path(__file__).resolve().parent / "g1.json"
RING_FLOOR_CM = 3.0

Kind = Literal["stop", "pause"]
Reason = Literal["stale_frame", "hand_lost", "covered", "contact", "distance", "ttc"]


@dataclass(frozen=True)
class G1Event:
    t_ms: float
    kind: Kind
    reason: Reason
    gap_cm: float | None = None


def load(path: Path = G1_PATH) -> G1File:
    g1 = G1File.model_validate(json.loads(path.read_text()))
    if g1.constants.ring_floor_cm < RING_FLOOR_CM:
        raise ValueError(f"ring_floor_cm {g1.constants.ring_floor_cm} is below the {RING_FLOOR_CM} cm floor")
    return g1


def _inside(p: Point, poly: list[Point]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        a, b = poly[i], poly[j]
        if (a.y > p.y) != (b.y > p.y) and p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x:
            inside = not inside
        j = i
    return inside


def _seg_dist(p: Point, a: Point, b: Point) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2))
    return math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))


def dist_px(p: Point, poly: list[Point]) -> float:
    """Distance to the polygon's edge in pixels; 0 inside."""
    if _inside(p, poly):
        return 0.0
    return min(_seg_dist(p, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def ring_cm(zone: Zone, c: G1Constants) -> float:
    return max(zone.ring_cm, c.ring_floor_cm, RING_FLOOR_CM)


def gap_cm(p: Point, frame: G1Frame, c: G1Constants) -> float:
    z = frame.zone
    return max(0.0, dist_px(p, z.polygon) * z.scale_cm_per_px - ring_cm(z, c) - frame.tool_len_cm)


class G1:
    """Feed frames in order; `step` returns the event for that frame, or None."""

    def __init__(self, constants: G1Constants) -> None:
        self.c = constants
        self._prev_t: float | None = None
        self._prev_gaps: list[float] = []

    def step(self, f: G1Frame) -> G1Event | None:
        c = self.c
        prev_t, prev_gaps = self._prev_t, self._prev_gaps
        gaps = [gap_cm(p, f, c) for p in f.fingertips]
        self._prev_t, self._prev_gaps = f.t_ms, gaps

        if (f.frame_age_ms or 0) > c.frame_max_age_ms:
            return G1Event(f.t_ms, "pause", "stale_frame")
        if not f.fingertips:
            if prev_gaps and min(prev_gaps) < c.near_cm:
                return G1Event(f.t_ms, "pause", "hand_lost")
            return None
        if any(_inside(p, f.zone.polygon) for p in f.fingertips):
            return G1Event(f.t_ms, "stop", "covered", 0.0)
        nearest = min(gaps)
        if nearest == 0:
            return G1Event(f.t_ms, "stop", "contact", 0.0)
        if not f.zone.ring_step and nearest < c.dist_cm:
            return G1Event(f.t_ms, "stop", "distance", nearest)
        if prev_t is not None and len(prev_gaps) == len(gaps) and f.t_ms > prev_t:
            dt = f.t_ms - prev_t
            for before, now in zip(prev_gaps, gaps, strict=True):
                closing = (before - now) / dt
                if closing > 0 and now / closing < c.ttc_ms:
                    return G1Event(f.t_ms, "stop", "ttc", now)
        return None


def run(frames: list[G1Frame], constants: G1Constants) -> G1Event | None:
    """The first event over a sequence of frames, or None."""
    g1 = G1(constants)
    for f in frames:
        event = g1.step(f)
        if event is not None:
            return event
    return None
