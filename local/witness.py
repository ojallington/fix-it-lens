"""Local mode's second witness, and the choice between the two.

Local mode's second witness is TRACK_LABEL: a separate Glimmer locate on NOW_2, plus optical flow run on the server
over the step's 2 s of clip frames. SAM 3.1 and Glimmer do not fit on one 32 GB GPU at once, so a local client is
given the local SAM server only with FIXIT_LOCAL_SAM=1 (eval, or a configuration without Glimmer); then
app/witness.py's SAM witness runs, as in api mode. The judge alone still never passes a step; this only replaces
the witness, and its eval row is reported apart from the SAM row.

The rules mirror app/witness.py's judge_tracks, with one difference said out loud: the flow follows the PLACE the
part belongs (its anchor, else the part's outline at step start), so "stable" means the view of that place held
steady over the clip, not that SAM saw the part itself sit still.
- present: the place is tracked through the clip, steady, and Glimmer finds the part overlapping where the place
  is now;
- absent: the place is tracked, and Glimmer does not find the part there.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import cv2
import numpy as np

TRACK_LABEL = "Checked without SAM (Glimmer and motion tracking)"
Box = tuple[int, int, int, int]  # 0-1000 grid
Result = Literal["done", "not_done", "cant_see"]

# The same numbers as app/witness.py, so the two witnesses differ in what they see, not in what they demand.
IN_PLACE = 0.5
STABLE_1000 = 50
PRESENT_FRACTION = 0.8
# The flow: features inside the place's box, Lucas-Kanade with a forward-backward check.
MIN_POINTS = 6
KEEP_FRACTION = 0.3
FB_MAX_PX = 1.0

LOCATE_FORMAT: dict[str, Any] = {
    "type": "json_schema", "name": "TrackLocate", "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False, "required": ["found", "box"],
        "properties": {
            "found": {"type": "boolean"},
            "box": {"anyOf": [{"type": "null"}, {"type": "array", "items": {"type": "integer", "minimum": 0,
                                                                              "maximum": 1000},
                                                  "minItems": 4, "maxItems": 4}]},
        },
    },
}
LOCATE_PROMPT = """Find the {name} in this photo of a household appliance.
If you can see it, found = true and box = [x1, y1, x2, y2] tightly around it, on a 0-1000 grid (x right, y down).
If it is not in the photo, found = false and box = null. Never guess a box for something you cannot see."""


def box_overlap(a: Box, b: Box) -> float:
    """Intersection over the area of a (as app.geometry.box_overlap)."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max(1, (a[2] - a[0]) * (a[3] - a[1]))


@dataclass(frozen=True)
class Track:
    boxes: list[Box]  # one per frame while tracked, from the first frame on
    n_frames: int

    @property
    def held(self) -> float:
        return len(self.boxes) / max(1, self.n_frames)

    @property
    def drift(self) -> float:
        c = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in self.boxes]
        return max((abs(p[0] - q[0]) + abs(p[1] - q[1]) for p in c for q in c), default=0.0)


def _gray(jpeg: bytes) -> np.ndarray[Any, Any]:
    im = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if im is None:
        raise ValueError("not a JPEG")
    return im


def flow_track(jpegs: Sequence[bytes], box: Box) -> Track:
    """Follow `box` from the first frame through the rest: median shift and scale of the features inside it."""
    frames = [_gray(j) for j in jpegs]
    if not frames:
        return Track([], 0)
    h, w = frames[0].shape
    sx, sy = w / 1000, h / 1000
    x1, y1, x2, y2 = box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy
    mask = np.zeros((h, w), np.uint8)
    mask[int(y1):int(y2) + 1, int(x1):int(x2) + 1] = 255
    pts = cv2.goodFeaturesToTrack(frames[0], maxCorners=120, qualityLevel=0.01, minDistance=4, mask=mask)
    if pts is None or len(pts) < MIN_POINTS:
        return Track([], len(frames))
    n0 = len(pts)
    cur = np.array([x1, y1, x2, y2], np.float64)
    boxes: list[Box] = [box]
    prev = frames[0]
    for g in frames[1:]:
        if g.shape != prev.shape:
            break
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, pts, None)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(g, prev, nxt, None)
        fb = np.linalg.norm((pts - back).reshape(-1, 2), axis=1)
        keep = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < FB_MAX_PX)
        if keep.sum() < max(MIN_POINTS, KEEP_FRACTION * n0):
            break
        a, b = pts.reshape(-1, 2)[keep], nxt.reshape(-1, 2)[keep]
        ca, cb = a.mean(axis=0), b.mean(axis=0)
        da, db = np.linalg.norm(a - ca, axis=1), np.linalg.norm(b - cb, axis=1)
        scale = float(np.median(db[da > 1e-3] / da[da > 1e-3])) if (da > 1e-3).any() else 1.0
        shift = np.median(b - a, axis=0)
        centre = (cur[:2] + cur[2:]) / 2 + shift
        half = (cur[2:] - cur[:2]) / 2 * scale
        cur = np.concatenate([centre - half, centre + half])
        boxes.append((max(0, round(cur[0] / sx)), max(0, round(cur[1] / sy)),
                      min(1000, round(cur[2] / sx)), min(1000, round(cur[3] / sy))))
        pts, prev = b.reshape(-1, 1, 2).astype(np.float32), g
    return Track(boxes, len(frames))


def decide(expect: Literal["present", "absent"], place: Track, located: Box | None) -> tuple[Result, str]:
    if place.held < PRESENT_FRACTION:
        return "cant_see", "the track lost the place the part belongs"
    if place.drift > STABLE_1000:
        return "cant_see", "the view of the place was not steady"
    now = place.boxes[-1]
    there = located is not None and box_overlap(located, now) >= IN_PLACE
    if expect == "absent":
        if there:
            return "not_done", "Glimmer still finds the part in its place"
        return "done", "the place is in view and Glimmer does not find the part there"
    if located is None:
        return "cant_see", "Glimmer did not find the part"
    if not there:
        return "not_done", "the part is not where it belongs"
    return "done", "Glimmer finds the part in place, and the view held steady"


def _located(text: str) -> Box | None:
    try:
        out = json.loads(text)
    except ValueError:
        return None
    b = out.get("box") if isinstance(out, dict) and out.get("found") is True else None
    if not (isinstance(b, list) and len(b) == 4 and all(isinstance(v, int) for v in b) and b[0] < b[2]
            and b[1] < b[3]):
        return None
    return (b[0], b[1], b[2], b[3])


def locate_input(name: str, now2_jpeg: bytes) -> list[dict[str, Any]]:
    from calls.images import prepare

    im = prepare(now2_jpeg, 1024)
    return [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": LOCATE_PROMPT.format(name=name)},
        {"type": "input_image", "image_url": im.data_url(), "detail": "high"}]}]


async def track_witness(models: Any, clip_frames: list[Any], now2: Any, spec: Any, part: Any, anchor: Any,
                        sam_text: str, judge_done: asyncio.Future[Any]) -> Any:
    """app.session's witness_provider signature; returns app.verdict.SamWitness with ran = "track"."""
    import calls
    from app.verdict import SamWitness

    if spec is None:
        return SamWitness("none", None, "this step names no part to witness")
    place = anchor if anchor is not None else part
    if place is None:
        return SamWitness("track", "cant_see", f"{TRACK_LABEL}: no outline of the place to track")
    try:
        r, _ = await models.respond("locate", locate_input(sam_text, now2.jpeg), format=LOCATE_FORMAT)
    except calls.CallError as e:
        return SamWitness("none", None, f"Glimmer locate failed: {type(e).__name__}")
    frames = [f.jpeg for f in clip_frames] or [now2.jpeg]
    track = await asyncio.to_thread(flow_track, frames, tuple(place.box))
    result, why = decide(spec.expect, track, _located(r.text))
    return SamWitness("track", result, f"{TRACK_LABEL}: {why}")


async def sam_weights_served(client: Any) -> bool:
    """True when local/sam_server answers /health with the weights present."""
    import httpx

    backend = getattr(client, "backend", None)
    url = getattr(backend, "sam_url", "")
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=2.0, transport=getattr(backend, "transport", None)) as h:
            return bool((await h.get(url + "/health")).json().get("weights"))
    except (httpx.HTTPError, ValueError):
        return False


async def local_witness(models: Any, clip_frames: list[Any], now2: Any, spec: Any, part: Any, anchor: Any,
                        sam_text: str, judge_done: asyncio.Future[Any]) -> Any:
    """The witness_provider for local mode, and for a Breaker session once it has switched: app/witness.py's SAM
    witness while the API is in use or the local SAM weights are served (probed once per backend), else the track
    witness."""
    from app.witness import sam_witness
    from calls import Breaker

    client = models.client
    if isinstance(client, Breaker) and client.mode == "api":
        fn = sam_witness
    else:
        fn = await provider(client.active if isinstance(client, Breaker) else client)
    return await fn(models, clip_frames, now2, spec, part, anchor, sam_text, judge_done)


async def provider(client: Any) -> Any:
    """The witness for a local client: app/witness.py's SAM witness when its SAM weights are served (probed once),
    else the track witness. app/session.py awaits this when its Breaker switches."""
    from app.witness import sam_witness

    b = getattr(client, "backend", None)
    if b is None:  # not a calls.Client (a test fake): no SAM server to ask
        return track_witness
    if getattr(b, "sam_served", None) is None:
        b.sam_served = await sam_weights_served(client)
    return sam_witness if b.sam_served else track_witness
