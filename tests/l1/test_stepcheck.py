"""check_clip (the eval's entry to the production step check) and its ablation switches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import calls
from app.stepcheck import WholeStep, check_clip
from contracts.models.card import Card
from tests.l1.helpers import FakeClient, jpeg

ROOT = Path(__file__).resolve().parents[2]
MASK = "M 200 200 L 400 200 L 400 400 L 200 400 Z"


def acme() -> Card:
    return Card.model_validate(json.loads((ROOT / "contracts/examples/card/acme-wm-0000.json").read_text()))


class Judge(FakeClient):
    def __init__(self, point: list[int], **kw: Any) -> None:
        obj = calls.SamObject("o1", (200, 200, 400, 400), 0.9, MASK, 0)
        track = [calls.SamObject("o1", (200, 200, 400, 400), 0.9, MASK, i) for i in range(40)]
        super().__init__(image=calls.SamResult(1000, 1000, [obj], "", 5.0, 0.002, "0" * 64),
                         video=calls.SamResult(1000, 1000, track, "", 5.0, 0.01, "0" * 64), **kw)
        self.point = point
        self.inputs: list[Any] = []

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        self.called.append(role)
        self.inputs.append(input)
        text = input[1]["content"][0]["text"]
        ids = [line[2:].split(":")[0] for line in text.splitlines() if line.startswith("- ")]
        out = {"clauses": [{"clause_id": i, "visible": True, "state": "satisfied", "evidence": "aligned",
                            "point": self.point} for i in ids]}
        return calls.Response(role, "m", "completed", json.dumps(out), [], [], calls.Usage(), 900.0, None, 0.003,
                              "1" * 64, 1, "c1")


def clip() -> list[tuple[float, bytes]]:
    return [(i * 0.1, jpeg(shade=100 + i)) for i in range(20)]


async def test_check_clip_done_and_costs(tmp_path: Path) -> None:
    c = Judge([300, 300])
    v, out, usd = await check_clip(acme(), "refit_filter", jpeg(shade=40), clip(), client=c)  # type: ignore[arg-type]
    assert v.result == "done" and v.witnesses.sam in ("video", "frame") and out is not None
    assert usd > 0


async def test_point_outside_the_part_is_not_done() -> None:
    v, _, _ = await check_clip(acme(), "refit_filter", jpeg(), clip(), client=Judge([900, 900]))  # type: ignore[arg-type]
    assert v.result == "cant_see"


async def test_no_witness_never_passes() -> None:
    v, out, _ = await check_clip(acme(), "refit_filter", jpeg(), clip(), witness="none",
                                 client=Judge([300, 300]))  # type: ignore[arg-type]
    assert out is not None and v.witnesses.judge == "done" and v.result != "done"


async def test_ablation_switches() -> None:
    c = Judge([900, 900])
    v, out, _ = await check_clip(acme(), "refit_filter", jpeg(), clip(), crop=False, clauses=False,
                                 client=c)  # type: ignore[arg-type]
    content = c.inputs[0][1]["content"]
    assert not any("CROP" in x.get("text", "") for x in content)  # no crop image
    assert out is not None and [x.clause_id for x in out.clauses] == [WholeStep]
    assert v.witnesses.judge == "done"  # the whole frame stands for the part: any point is on it


async def test_appearance_part_falls_back_to_the_locate_box_when_sam_fails(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from app import locate
    from app.models import Models
    from app.recorder import Recorder
    from contracts import VERSION
    from contracts.models.session import SessionMeta
    from tests.l1.helpers import frame

    class NoSam(FakeClient):
        async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
            raise calls.BackendUnavailable("no SAM server")

        async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
            out = {"found": True, "cue_seen": True, "box": [100, 100, 300, 300], "note": ""}
            return calls.Response(role, "m", "completed", json.dumps(out), [], [], calls.Usage(), 500.0, None, 0.0,
                                  "3" * 64, 1, "c")

    meta = SessionMeta(session_id="x", started_at=datetime.now(UTC), mode="local", lang="en",
                       contracts_version=VERSION)
    m = Models(NoSam(), Recorder(None, meta), lambda: 0.0, household=None, session_id="x")  # type: ignore[arg-type]
    part = next(p for p in acme().parts if p.id == "debris_filter")
    out, found = await locate.outline(m, part, frame(1, 0), "en")
    assert found is not None and out.source == "box" and out.region is not None and out.region.box[0] < 100
    red = next(p for p in acme().parts if p.kind == "red_zone")
    assert red.locate == "function"  # red zones go through the locate role, never SAM by text



async def test_appearance_part_with_no_sam_object_is_drawn_from_the_locate_box(tmp_path: Path) -> None:
    """M1: SAM by text found 0 objects for the filter in every call, so nothing was drawn and the check had no part."""
    from datetime import UTC, datetime

    from app import locate
    from app.models import Models
    from app.recorder import Recorder
    from contracts import VERSION
    from contracts.models.session import SessionMeta
    from tests.l1.helpers import frame

    class Located(FakeClient):
        async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
            self.called.append(role)
            out = {"found": True, "cue_seen": False, "box": [100, 100, 300, 300], "note": ""}
            return calls.Response(role, "m", "completed", json.dumps(out), [], [], calls.Usage(), 500.0, None, 0.0,
                                  "3" * 64, 1, "c")

    meta = SessionMeta(session_id="x", started_at=datetime.now(UTC), mode="api", lang="en",
                       contracts_version=VERSION)
    part = next(p for p in acme().parts if p.id == "debris_filter")
    weak = calls.SamObject("o1", (200, 200, 400, 400), 0.3, MASK, 0)
    for found in ([], [weak]):  # no object, or none confident
        c = Located(image=calls.SamResult(1000, 1000, found, "", 5.0, 0.002, "0" * 64))
        m = Models(c, Recorder(None, meta), lambda: 0.0, household=None, session_id="x")  # type: ignore[arg-type]
        out, where = await locate.outline(m, part, frame(1, 0), "en")
        assert c.called == ["sam_image", "locate", "sam_image"]  # by text, the locate box, SAM from that box
        assert where is not None and out.source == "box" and out.region is not None
        assert out.region.source == "box" and out.region.box[0] < 100 and out.region.box[2] > 300  # drawn larger
        assert out.region.svg().startswith("M")  # the outline message carries it as a path the phone draws

class Malformed(Judge):
    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        self.called.append(role)
        return calls.Response(role, "m", "completed", "{not json", [], [], calls.Usage(), 900.0, None, 0.0,
                              "4" * 64, 1, "c")


async def test_malformed_judge_reply_retries_once_then_cant_see() -> None:
    c = Malformed([300, 300])
    v, out, _ = await check_clip(acme(), "refit_filter", jpeg(), clip(), client=c)  # type: ignore[arg-type]
    assert out is None and v.result == "cant_see" and v.witnesses.judge == "cant_see"
    assert c.called.count("judge") == 2


async def test_locate_with_no_box_draws_nothing(tmp_path: Path) -> None:
    from app.session import Session
    from tests.l1.helpers import frame

    class NoBox(FakeClient):
        async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
            out = {"found": False, "cue_seen": False, "box": None, "note": "not in view"}
            return calls.Response(role, "m", "completed", json.dumps(out), [], [], calls.Usage(), 500.0, None, 0.0,
                                  "5" * 64, 1, "c")

    data = json.loads((ROOT / "contracts/examples/card/acme-wm-0000.json").read_text())
    for z in data["red_zones"]:
        z["rule_id"] = "nl.mains.casing"
    s = Session(session_id="nb", client=NoBox(), card=Card.model_validate(data), job_id="clean_debris_filter",
                country="nl", record_root=tmp_path)  # type: ignore[arg-type]
    sent: list[dict[str, Any]] = []

    async def sink(m: dict[str, Any]) -> None:
        sent.append(m)

    s.sinks.append(sink)
    s.frames.add(frame(1, 0))
    out = await s.outline_part("back_panel")
    assert out is not None and out.region is None
    assert not any(m["type"] in ("outline", "zone") for m in sent)
    assert any("ask for a better view" in e for e in s.events)


@pytest.mark.parametrize(("images", "point", "result"), [
    ("PLUG_OUT", [150, 650], "done"),  # judge points at the plug, SAM sees it out
    ("PLUG_OUT", [900, 100], "cant_see"),  # a point off the plug and the socket is never done
    ("PLUG_IN", [450, 450], "not_done"),  # the judge is fooled; SAM sees the plug in
])
async def test_check_clip_grounds_unplug_like_the_session(images: str, point: list[int], result: str) -> None:
    """The eval measures what Session.check does on a no-part power step (both call stepcheck.power_ground)."""
    from tests.l1 import test_session

    c = Judge(point)
    c.images = getattr(test_session, images)
    v, _, _ = await check_clip(acme(), "unplug", jpeg(), clip(), client=c)  # type: ignore[arg-type]
    assert v.result == result and v.witnesses.sam == "frame"


async def test_check_clip_unplug_with_no_witness_never_passes() -> None:
    from tests.l1.test_session import PLUG_OUT

    c = Judge([150, 650])
    c.images = PLUG_OUT
    v, _, _ = await check_clip(acme(), "unplug", jpeg(), clip(), witness="none", client=c)  # type: ignore[arg-type]
    assert v.witnesses.judge == "done" and v.result != "done"
