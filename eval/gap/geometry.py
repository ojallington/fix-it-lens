"""2-D geometry on SAM masks, with no numpy: SVG mask paths to polygons, the gap between two masks, the scale from
a part of known width. The slider camera sees the hand and the tap in profile, so the gap is a 2-D measure with
its error stated in the README.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from PIL import Image, ImageChops, ImageDraw

Point = tuple[float, float]
Polygon = list[Point]

_TOKEN = re.compile(r"[MLZmlz]|-?\d+(?:\.\d+)?")


def svg_polygons(path: str) -> list[Polygon]:
    """Absolute M/L/Z paths (what calls/ returns for a SAM mask) to closed polygons."""
    polys: list[Polygon] = []
    cur: Polygon = []
    toks = _TOKEN.findall(path)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("M", "m"):
            if cur:
                polys.append(cur)
            cur = []
            i += 1
        elif t in ("L", "l"):
            i += 1
        elif t in ("Z", "z"):
            if cur:
                polys.append(cur)
            cur = []
            i += 1
        else:
            cur.append((float(toks[i]), float(toks[i + 1])))
            i += 2
    if cur:
        polys.append(cur)
    return [p for p in polys if len(p) >= 3]


def raster(polys: Sequence[Polygon], size: tuple[int, int]) -> Image.Image:
    im = Image.new("1", size, 0)
    d = ImageDraw.Draw(im)
    for p in polys:
        d.polygon(p, fill=1)
    return im


def overlap(a: Sequence[Polygon], b: Sequence[Polygon], size: tuple[int, int]) -> bool:
    return ImageChops.logical_and(raster(a, size), raster(b, size)).getbbox() is not None


def _seg_dist(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _edges(polys: Sequence[Polygon]) -> list[tuple[Point, Point]]:
    return [(p[i], p[(i + 1) % len(p)]) for p in polys for i in range(len(p))]


def point_to_mask(p: Point, polys: Sequence[Polygon]) -> float:
    return min(_seg_dist(p, a, b) for a, b in _edges(polys))


def mask_gap_px(a: Sequence[Polygon], b: Sequence[Polygon], size: tuple[int, int]) -> float:
    """Smallest distance in pixels between two masks; 0 when they touch or overlap."""
    if overlap(a, b, size):
        return 0.0
    ea, eb = _edges(a), _edges(b)
    best = math.inf
    for p in (v for poly in a for v in poly):
        best = min(best, min(_seg_dist(p, x, y) for x, y in eb))
    for p in (v for poly in b for v in poly):
        best = min(best, min(_seg_dist(p, x, y) for x, y in ea))
    return best


def width_px(polys: Sequence[Polygon]) -> float:
    xs = [x for p in polys for x, _ in p]
    return max(xs) - min(xs)


def gap_cm(gap_px: float, ref_width_px: float, ref_width_mm: float) -> float:
    """Scale a pixel gap by a part of known width in the same plane (the tap's handle or the panel screw head)."""
    if ref_width_px <= 0:
        raise ValueError("reference part has no width in the image")
    return gap_px * ref_width_mm / ref_width_px / 10.0
