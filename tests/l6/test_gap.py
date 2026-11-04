import pytest

from calls import SamObject
from eval.gap.geometry import gap_cm, mask_gap_px, point_to_mask, svg_polygons, width_px
from eval.gap.measure import GapResult, Stop, claims, measure


def sq(x1: float, y1: float, x2: float, y2: float) -> str:
    return f"M{x1} {y1}L{x2} {y1}L{x2} {y2}L{x1} {y2}Z"


def test_svg_parse_matches_calls_output_shape() -> None:
    # The shape calls/ returns: absolute M/L with half-pixel corners, sometimes several subpaths
    polys = svg_polygons("M9.5 20L10 19.5L29.5 19.5L29.5 39.5L9.5 39.5ZM50 50L60 50L60 60Z")
    assert len(polys) == 2 and polys[0][0] == (9.5, 20.0)


def test_gap_between_squares_and_overlap() -> None:
    a, b = svg_polygons(sq(0, 0, 10, 10)), svg_polygons(sq(30, 0, 40, 10))
    assert mask_gap_px(a, b, (64, 64)) == pytest.approx(20.0)
    assert mask_gap_px(a, svg_polygons(sq(5, 5, 20, 20)), (64, 64)) == 0.0
    assert point_to_mask((20.0, 5.0), b) == pytest.approx(10.0)
    assert width_px(b) == 10.0


def test_scale_by_known_width() -> None:
    # tap 52 mm wide spans 104 px: 0.5 mm/px, so 60 px is 3.0 cm
    assert gap_cm(60, 104, 52.0) == pytest.approx(3.0)
    with pytest.raises(ValueError):
        gap_cm(1, 0, 52.0)


def _obj(svg: str, frame: int, conf: float = 0.9) -> SamObject:
    return SamObject(object_id="1", box_px=(0, 0, 1, 1), conf=conf, mask_svg=svg, frame=frame)


def test_measure_sam_then_hand_label_then_none() -> None:
    stop = Stop("gas.r01", "gas", "c.mp4", 5, phone_gap_cm=3.5, ref_prompt="gas tap", ref_width_mm=50.0)
    tap = [_obj(sq(100, 0, 200, 50), 5), _obj(sq(0, 0, 1, 1), 5, conf=0.1)]  # the confident mask is the tap
    hand = [_obj(sq(0, 0, 40, 40), 5), _obj(sq(0, 0, 99, 40), 4)]            # frame 4 is not the stop
    r = measure(stop, hand, tap, (256, 64))
    assert r.source == "sam" and r.slider_gap_cm == pytest.approx(3.0) and r.error_cm == pytest.approx(0.5)
    labelled = Stop(**{**stop.__dict__, "hand_point": (80.0, 10.0)})
    r2 = measure(labelled, [], tap, (256, 64))
    assert r2.source == "hand_label" and r2.slider_gap_cm == pytest.approx(1.0)
    assert measure(stop, [], tap, (256, 64)).source == "none"


def test_claims_mark_sources() -> None:
    rs = [GapResult("a", "gas", 3.0, "sam", 3.5), GapResult("b", "gas", 1.0, "hand_label", 0.0),
          GapResult("c", "gas", None, "none", 2.0)]
    c = {x["id"]: x for x in claims("gas", rs)}
    assert c["gap.gas.measured"]["value"] == 2 and c["gap.gas.measured"]["n"] == 3
    assert "1 from SAM masks, 1 from hand labels" in c["gap.gas.measured"]["note"]
    assert c["gap.gas.median_cm"]["value"] == 2.0
    assert c["gap.gas.phone_error_median_cm"]["value"] == 0.8  # |0.5| and |-1.0|
