"""The drawn outline is simplified to at most 200 points per polygon; the region's own check keeps the full mask."""

from __future__ import annotations

import math

from app.geometry import MAX_DRAWN_POINTS, Region, parse_path, simplify


def circle(n: int, r: float = 300.0, c: float = 500.0) -> list[tuple[float, float]]:
    return [(c + r * math.cos(2 * math.pi * i / n), c + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def area(p: list[tuple[float, float]]) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(p, p[1:] + p[:1], strict=True))) / 2


def test_a_dense_mask_is_drawn_with_at_most_200_points_and_keeps_its_shape() -> None:
    dense = circle(4000)
    out = simplify(dense)
    assert 3 <= len(out) <= MAX_DRAWN_POINTS
    assert abs(area(out) - area(dense)) / area(dense) < 0.01


def test_a_small_mask_is_drawn_as_it_is() -> None:
    small = circle(50)
    assert simplify(small) == small


def test_region_draws_simplified_but_checks_on_the_full_mask() -> None:
    dense = circle(4000)
    svg = "M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in dense) + "Z"
    r = Region.from_mask("f1", 1000, 1000, (200, 200, 800, 800), svg, 0.9)
    assert len(r.polys[0]) == 4000
    drawn = parse_path(r.svg())
    assert len(drawn) == 1 and len(drawn[0]) <= MAX_DRAWN_POINTS
    assert r.contains(500, 500) and not r.contains(850, 850)
