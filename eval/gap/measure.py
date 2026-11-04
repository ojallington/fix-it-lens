"""The real gap at the stop: SAM 3.1 video on the slider camera's side clip segments the hand and the part
(the gas tap, or the washer's back-panel screw), the part's known width gives the scale, and the fingertip-to-part
gap at each STOP is set against the phone's own distance estimate as an error in cm.

    python -m eval.gap run [--kind gas]     # SAM video per stop clip; FIXIT_CALLS=replay (default) or record
    python -m eval.gap score                # claims from eval/results/gap/*.jsonl

The stops are listed in eval/labels/gap.jsonl, one line each:
    {"stop_id": "gas.r07", "kind": "gas", "clip": "gas.r07.mp4", "stop_frame": 61, "phone_gap_cm": 4.2,
     "ref_prompt": "gas tap", "ref_width_mm": 52.0, "hand_point": null}
`stop_frame` is the slider frame at the phone's STOP, synced on the torch flash at the start of each recording (the
flash is a brightness spike in both the slider clip and the phone's frames, so no shared clock is needed).
`phone_gap_cm` is the phone's distance to the part itself, computed only by eval.phone.phone_gap_cm (the c2s g1
event's dist_cm to the ring edge, plus max(ring_cm, 3), plus tool_len_cm from that zone's s2c message; a covered stop
with null dist_cm counts as 0 to the ring). `python -m eval.phone gap` fills it into each line that names its
session_id and frame_id, so no distance is typed.
`hand_point` is the hand label [x, y] on that frame, used only where SAM's hand mask drops; the table marks which.
Clips are read from FIXIT_PRIVATE/clips-raw/slider/.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from eval.gap.geometry import Polygon, gap_cm, mask_gap_px, point_to_mask, svg_polygons, width_px
from eval.stats import median, percentile

EVAL = Path(__file__).resolve().parent.parent
STOPS = EVAL / "labels" / "gap.jsonl"
RESULTS = EVAL / "results" / "gap"


@dataclass(frozen=True)
class Stop:
    stop_id: str
    kind: str
    clip: str
    stop_frame: int
    phone_gap_cm: float | None
    ref_prompt: str
    ref_width_mm: float
    hand_point: tuple[float, float] | None = None


@dataclass(frozen=True)
class GapResult:
    stop_id: str
    kind: str
    slider_gap_cm: float | None
    source: str  # sam | hand_label | none
    phone_gap_cm: float | None

    @property
    def error_cm(self) -> float | None:
        if self.slider_gap_cm is None or self.phone_gap_cm is None:
            return None
        return round(self.phone_gap_cm - self.slider_gap_cm, 2)


def load_stops(path: Path = STOPS) -> list[Stop]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            hp = d.get("hand_point")
            out.append(Stop(**{**d, "hand_point": tuple(hp) if hp else None}))
    return out


def frame_polys(objects: list[Any], frame: int) -> list[tuple[float, list[Polygon]]]:
    """(conf, polygons) for each SAM object on one video frame."""
    return [(o.conf or 0.0, svg_polygons(o.mask_svg)) for o in objects
            if o.frame == frame and o.mask_svg]


def measure(stop: Stop, hand_objs: list[Any], ref_objs: list[Any], size: tuple[int, int]) -> GapResult:
    refs = frame_polys(ref_objs, stop.stop_frame)
    if not refs:
        return GapResult(stop.stop_id, stop.kind, None, "none", stop.phone_gap_cm)
    ref = max(refs, key=lambda r: r[0])[1]
    scale_px = width_px(ref)
    hands = [p for _, polys in frame_polys(hand_objs, stop.stop_frame) for p in polys]
    if hands:
        px, source = mask_gap_px(hands, ref, size), "sam"
    elif stop.hand_point is not None:
        px, source = point_to_mask(stop.hand_point, ref), "hand_label"
    else:
        return GapResult(stop.stop_id, stop.kind, None, "none", stop.phone_gap_cm)
    return GapResult(stop.stop_id, stop.kind, round(gap_cm(px, scale_px, stop.ref_width_mm), 2), source,
                     stop.phone_gap_cm)


async def run(stops: list[Stop]) -> list[GapResult]:
    import calls  # only the runner calls a model

    priv = os.environ.get("FIXIT_PRIVATE")
    root = (Path(priv) if priv else Path.home() / "Projects/hackathons/fix-it-lens-private") / "clips-raw" / "slider"
    out = []
    for s in stops:
        clip = (root / s.clip).read_bytes()
        hand = await calls.sam_video(clip, "hand", lane="l6")
        ref = await calls.sam_video(clip, s.ref_prompt, lane="l6")
        out.append(measure(s, hand.objects, ref.objects, (ref.width, ref.height)))
    return out


def write_results(kind: str, results: list[GapResult], root: Path = RESULTS) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{kind}.jsonl"
    p.write_text("".join(json.dumps({**asdict(r), "error_cm": r.error_cm}) + "\n"
                         for r in sorted(results, key=lambda r: r.stop_id)))
    return p


def load_results(kind: str, root: Path = RESULTS) -> list[GapResult]:
    p = root / f"{kind}.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return [GapResult(r["stop_id"], r["kind"], r["slider_gap_cm"], r["source"], r["phone_gap_cm"]) for r in rows]


def claims(kind: str, results: list[GapResult]) -> list[dict[str, Any]]:
    measured = [r for r in results if r.slider_gap_cm is not None]
    gaps = [r.slider_gap_cm for r in measured if r.slider_gap_cm is not None]
    errs = [abs(e) for r in measured if (e := r.error_cm) is not None]
    n_sam = sum(1 for r in measured if r.source == "sam")
    pre = f"gap.{kind}"
    out: list[dict[str, Any]] = [
        {"id": f"{pre}.measured", "value": len(measured), "unit": "stops of n measured", "n": len(results),
         "ci": None,
         "note": f"{n_sam} from SAM masks, {len(measured) - n_sam} from hand labels where the mask dropped"},
    ]
    if gaps:
        out += [
            {"id": f"{pre}.median_cm", "value": round(median(gaps), 1), "unit": "cm", "n": len(gaps), "ci": None},
            {"id": f"{pre}.min_cm", "value": round(min(gaps), 1), "unit": "cm", "n": len(gaps), "ci": None},
        ]
    if errs:
        out += [
            {"id": f"{pre}.phone_error_median_cm", "value": round(median(errs), 1), "unit": "cm (absolute)",
             "n": len(errs), "ci": None},
            {"id": f"{pre}.phone_error_p95_cm", "value": round(percentile(errs, 95), 1), "unit": "cm (absolute)",
             "n": len(errs), "ci": None},
        ]
    return out
