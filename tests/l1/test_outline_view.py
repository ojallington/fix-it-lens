"""An appearance part SAM misses by its words: SAM is asked again from the locate box before the plain box is drawn.
A box that reaches three edges of the frame is the view itself (the inside of a dishwasher seen through the open
door): the check keeps it as the part's region, and the phone is sent no outline tracing the screen's edges. A red
zone is always drawn."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import calls
from app import locate
from app.geometry import Region
from app.models import Models
from app.recorder import Recorder
from tests.l1.helpers import FakeClient, frame
from tests.l1.test_session import acme, session

MASK = "M 200 200 L 400 200 L 400 400 L 200 400 Z"
NOTHING = calls.SamResult(1000, 1000, [], "", 5.0, 0.002, "0" * 64)
MASKED = calls.SamResult(1000, 1000, [calls.SamObject("o1", (200, 200, 400, 400), 0.9, MASK, 0)], "", 5.0, 0.002,
                         "1" * 64)


class Boxed(FakeClient):
    """Locate answers with one box; SAM by the part's words finds nothing, SAM from a box gives `image`."""

    def __init__(self, box: list[int], image: calls.SamResult, words: str) -> None:
        super().__init__(image=image, images={words: NOTHING})
        self.box = box

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        self.called.append(role)
        out = {"found": True, "cue_seen": True, "box": self.box, "note": ""}
        return calls.Response(role, "m", "completed", json.dumps(out), [], [], calls.Usage(), 500.0, None, 0.0,
                              "3" * 64, 1, "c")


def models(c: FakeClient) -> Models:
    from datetime import UTC, datetime

    from contracts import VERSION
    from contracts.models.session import SessionMeta

    meta = SessionMeta(session_id="x", started_at=datetime.now(UTC), mode="api", lang="en", contracts_version=VERSION)
    return Models(c, Recorder(None, meta), lambda: 0.0, household=None, session_id="x")  # type: ignore[arg-type]


async def test_sam_outlines_from_the_locate_box_when_the_words_miss() -> None:
    part = next(p for p in acme().parts if p.id == "debris_filter")
    c = Boxed([150, 150, 450, 450], MASKED, part.sam_text or "")
    out, found = await locate.outline(models(c), part, frame(1, 0), "en")
    assert c.called == ["sam_image", "locate", "sam_image"]
    assert found is not None and out.source == "sam" and out.region is not None and out.region.polys


def test_a_box_on_three_edges_is_the_view() -> None:
    def box(b: tuple[int, int, int, int]) -> Region:
        return Region("f", 576, 1024, b, source="box")

    assert box((0, 0, 1000, 489)).spans_view()  # the M1 tub: full width, the top half
    assert box((0, 300, 1000, 1000)).spans_view()
    assert not box((0, 0, 500, 489)).spans_view()  # two edges: a part in a corner is drawn
    assert not box((100, 100, 900, 900)).spans_view()
    assert not Region.from_mask("f", 576, 1024, (0, 0, 1000, 1000), "M0 0L576 0L576 1024Z", 0.9).spans_view()


def outlines(s: Any) -> list[dict[str, Any]]:
    return [m for m in s.out if m["type"] == "outline"]


async def test_a_part_that_fills_the_view_keeps_its_region_and_draws_nothing(tmp_path: Path) -> None:
    part = next(p for p in acme().parts if p.id == "debris_filter")
    s = session(tmp_path, Boxed([0, 0, 1000, 425], NOTHING, part.sam_text or ""))
    s.start()
    f = frame(1, s.clock())
    s.frames.add(f)
    out = await s.outline_part("debris_filter", f)
    assert out is not None and out.region is not None and out.region.box == (0, 0, 1000, 489)
    assert s.regions["debris_filter"] is out.region  # the check still reads it
    assert outlines(s) == []
    [rail] = [m for m in s.out if m["type"] == "rail" and m["tool"] == "segment"]
    assert rail["result_short"] == "it fills the view"
    await s.close()


async def test_a_part_in_view_is_still_drawn(tmp_path: Path) -> None:
    part = next(p for p in acme().parts if p.id == "debris_filter")
    s = session(tmp_path, Boxed([150, 150, 450, 450], NOTHING, part.sam_text or ""))
    s.start()
    f = frame(1, s.clock())
    s.frames.add(f)
    await s.outline_part("debris_filter", f)
    assert [o["part_id"] for o in outlines(s)] == ["debris_filter"]
    await s.close()


async def test_a_red_zone_that_fills_the_view_is_still_drawn(tmp_path: Path) -> None:
    s = session(tmp_path, Boxed([0, 0, 1000, 425], NOTHING, ""))
    s.start()
    f = frame(1, s.clock())
    s.frames.add(f)
    await s.outline_part("back_panel", f)
    assert [(o["part_id"], o["kind"]) for o in outlines(s)] == [("back_panel", "do_not_touch")]
    await s.close()
