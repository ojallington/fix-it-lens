"""Masks and boxes. SAM masks arrive as SVG paths in the sent frame's pixels; the judge's points and Spark's boxes
are on the 0-1000 grid of a frame. A Region holds one part's outline on one frame and answers "is this point in it"."""

from __future__ import annotations

import re
from dataclasses import dataclass

Box = tuple[int, int, int, int]  # x_min, y_min, x_max, y_max on the 0-1000 grid
Poly = list[tuple[float, float]]

_TOKEN = re.compile(r"[MLZmlzHVhv]|-?\d+(?:\.\d+)?")


def parse_path(d: str) -> list[Poly]:
    """Absolute M/L/H/V/Z paths, as decode_mask_to_svg_path writes them, into closed polygons."""
    polys: list[Poly] = []
    cur: Poly = []
    cmd = "M"
    nums: list[float] = []
    x = y = 0.0

    def flush_point() -> None:
        nonlocal x, y
        if cmd in "ML" and len(nums) >= 2:
            x, y = nums[0], nums[1]
            cur.append((x, y))
            del nums[:2]
        elif cmd == "H" and nums:
            x = nums.pop(0)
            cur.append((x, y))
        elif cmd == "V" and nums:
            y = nums.pop(0)
            cur.append((x, y))

    for tok in _TOKEN.findall(d):
        if tok.isalpha():
            up = tok.upper()
            if up == "Z":
                if len(cur) >= 3:
                    polys.append(cur)
                cur = []
            elif up == "M" and len(cur) >= 3:
                polys.append(cur)
                cur = []
            cmd = up if up != "Z" else "M"
            continue
        nums.append(float(tok))
        flush_point()
    if len(cur) >= 3:
        polys.append(cur)
    return polys


def _inside(poly: Poly, px: float, py: float) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xc = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xc:
                inside = not inside
    return inside


def grow(box: Box, frac: float) -> Box:
    x1, y1, x2, y2 = box
    dx, dy = (x2 - x1) * frac, (y2 - y1) * frac
    return (max(0, round(x1 - dx)), max(0, round(y1 - dy)), min(1000, round(x2 + dx)), min(1000, round(y2 + dy)))


def box_valid(box: object) -> bool:
    return (isinstance(box, list | tuple) and len(box) == 4 and all(isinstance(v, int) and 0 <= v <= 1000 for v in box)
            and box[0] < box[2] and box[1] < box[3])


def in_box(box: Box, x: float, y: float) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def box_overlap(a: Box, b: Box) -> float:
    """Intersection over the area of a."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def box_svg(box: Box, width: int, height: int) -> str:
    x1, y1, x2, y2 = (v * s / 1000 for v, s in zip(box, (width, height, width, height), strict=True))
    return f"M{x1:g} {y1:g}L{x2:g} {y1:g}L{x2:g} {y2:g}L{x1:g} {y2:g}Z"


MAX_DRAWN_POINTS = 200  # per polygon of a drawn outline; the phone redraws it every frame


def _rdp(poly: Poly, eps: float) -> Poly:
    """Ramer-Douglas-Peucker on an open run of points: every dropped point is within eps px of the kept line."""
    keep = [False] * len(poly)
    keep[0] = keep[-1] = True
    stack = [(0, len(poly) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = poly[a], poly[b]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        far, at = -1.0, -1
        for i in range(a + 1, b):
            px, py = poly[i]
            d = abs(dy * (px - x1) - dx * (py - y1)) / norm if norm else ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
            if d > far:
                far, at = d, i
        if at >= 0 and far > eps:
            keep[at] = True
            stack += [(a, at), (at, b)]
    return [p for p, k in zip(poly, keep, strict=True) if k]


def simplify(poly: Poly, max_points: int = MAX_DRAWN_POINTS) -> Poly:
    """A closed polygon with at most max_points points, as close to the original as that allows: RDP with the
    tolerance doubled from half a pixel until it fits. Only the drawn outline is simplified; Region.contains keeps
    the full mask, so a verdict or a red-zone check never reads the simplified one."""
    if len(poly) <= max_points:
        return poly
    eps = 0.5
    while True:
        out = _rdp([*poly, poly[0]], eps)[:-1]  # closed: the first point ends the run too
        if len(out) <= max_points:
            return out
        eps *= 2


@dataclass(frozen=True)
class Region:
    """One part's outline on one frame: a SAM mask (polygons in pixels) or, failing that, a box."""

    frame_id: str
    width: int
    height: int
    box: Box  # 0-1000 grid
    polys: tuple[tuple[tuple[float, float], ...], ...] = ()
    conf: float | None = None
    source: str = "sam"  # sam | box (Spark's box, drawn larger)

    @classmethod
    def whole(cls, frame_id: str, width: int, height: int) -> Region:
        """The whole frame (the eval's no-crop ablation: no locate, so the frame stands for the part)."""
        return cls(frame_id, width, height, (0, 0, 1000, 1000), (), None, "frame")

    @classmethod
    def from_mask(cls, frame_id: str, width: int, height: int, box: Box, svg: str | None,
                  conf: float | None) -> Region:
        polys = tuple(tuple(p) for p in parse_path(svg)) if svg else ()
        return cls(frame_id, width, height, box, polys, conf, "sam" if polys else "box")

    def svg(self) -> str:
        if self.polys:
            return "".join("M" + "L".join(f"{x:g} {y:g}" for x, y in simplify(list(p))) + "Z" for p in self.polys)
        return box_svg(self.box, self.width, self.height)

    def spans_view(self) -> bool:
        """A box (no mask) that reaches three edges of the frame: the part is the view itself, and a box drawn
        around it would only trace the screen's edges."""
        if self.polys:
            return False
        x1, y1, x2, y2 = self.box
        return (x1 <= 0) + (y1 <= 0) + (x2 >= 1000) + (y2 >= 1000) >= 3

    def contains(self, x_1000: float, y_1000: float) -> bool:
        """Is a point on the 0-1000 grid of this region's frame inside the part?"""
        if not self.polys:
            return in_box(self.box, x_1000, y_1000)
        px, py = x_1000 * self.width / 1000, y_1000 * self.height / 1000
        return any(_inside(list(p), px, py) for p in self.polys)
