"""The two-witness rule: the drain cap in the hand is not done, the cap on the drain is, and a slow video
call falls back to a single-frame SAM check that the verdict names."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.geometry import Region
from app.models import Models
from app.recorder import Recorder
from app.verdict import JudgeFinding, combine
from app.witness import WitnessSpec, judge_tracks, sam_witness
from contracts.models.session import SessionMeta
from tests.l1.helpers import FakeClient, frame, sam_result

DRAIN = (400, 600, 600, 800)  # the drain's outline, 0-1000 grid
CAP = WitnessSpec("drain_cap", "present", "drain")
ON_DRAIN = [("1", i, (420.0, 620.0, 580.0, 780.0), 0.92) for i in range(10)]
IN_HAND = [("1", i, (100.0 + 20 * i, 100.0, 200.0 + 20 * i, 200.0), 0.9) for i in range(10)]


def models(client: Any) -> Models:
    meta = SessionMeta.model_validate({"session_id": "t", "started_at": "2026-10-27T10:00:00Z", "mode": "api",
                                       "lang": "en", "contracts_version": "1.1.0"})
    return Models(client, Recorder(None, meta), lambda: 0.0, household=None, session_id="t")


def test_cap_in_hand_is_not_done() -> None:
    result, why = judge_tracks(sam_result(IN_HAND), 10, CAP, DRAIN)
    assert result == "not_done", why
    v = combine("s", "c", 1, JudgeFinding("done", ["c1"], None, ""), sam_witness_of(result), None, 1)
    assert v.result != "done"


def test_cap_on_drain_is_done() -> None:
    assert judge_tracks(sam_result(ON_DRAIN), 10, CAP, DRAIN)[0] == "done"


def test_cap_found_in_few_frames_is_not_stable() -> None:
    assert judge_tracks(sam_result(ON_DRAIN[:3]), 10, CAP, DRAIN)[0] == "not_done"


def test_removed_part() -> None:
    gone = WitnessSpec("filter", "absent", "sump")
    assert judge_tracks(sam_result([]), 10, gone, DRAIN)[0] == "done"
    assert judge_tracks(sam_result(ON_DRAIN), 10, gone, DRAIN)[0] == "not_done"


def sam_witness_of(result: str) -> Any:
    from app.verdict import SamWitness

    return SamWitness("video", result)  # type: ignore[arg-type]


def clip() -> list[Any]:
    return [frame(i, 1000 + 200 * i) for i in range(10)]


@pytest.mark.usefixtures("ffmpeg")
async def test_video_slower_than_judge_falls_back_to_frame() -> None:
    client = FakeClient(video=sam_result(ON_DRAIN), image=sam_result([("1", None, (420.0, 620.0, 580.0, 780.0), 0.9)]),
                        video_delay=5)
    m = models(client)
    judge_done: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    judge_done.set_result(None)
    part = Region("f9", 640, 480, (420, 620, 580, 780))
    anchor = Region("f9", 640, 480, DRAIN)
    w = await sam_witness(m, clip(), clip()[-1], CAP, part, anchor, "cap", judge_done)
    assert w.ran == "frame" and w.result == "done"
    assert client.called == ["sam_video", "sam_image"]
    v = combine("s", "c", 1, JudgeFinding("done", ["c1"], None, ""), w, None, 1)
    assert v.witnesses.sam == "frame"


@pytest.mark.usefixtures("ffmpeg")
async def test_video_faster_than_judge_is_used() -> None:
    client = FakeClient(video=sam_result(IN_HAND))
    m = models(client)
    judge_done: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_later(0.5, judge_done.set_result, None)
    part = Region("f9", 640, 480, (420, 620, 580, 780))
    w = await sam_witness(m, clip(), clip()[-1], CAP, part, Region("f9", 640, 480, DRAIN), "cap", judge_done)
    assert w.ran == "video" and w.result == "not_done"
    assert [x["value"] for x in m.rec.lines if x["kind"] == "pick"] == ["video"]
