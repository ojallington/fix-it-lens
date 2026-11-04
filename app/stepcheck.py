"""One step check: the judge and the second witness run side by side, and code turns their findings into the verdict.

`judge_and_witness` is what the session's `check` runs. `check_clip` runs the same path on recorded frames, outside a
session, for the eval: no G0 is involved because nothing is shown or said, and no safety state moves.

The eval's ablation switches (`crop`, `clauses`, `witness="none"`) exist only on `check_clip`; the session never
passes them, and none of them can make a step pass that the production path would not: with no witness a step is
never done, and with no clause list the judge still has to see the step done and point at the part.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from PIL import Image

import calls
from app import config, judge, locate, power
from app import witness as wit
from app.frames import FrameBuffer, StoredFrame
from app.geometry import Region
from app.models import Models
from app.ports import HomeAssistant, NoHomeAssistant
from app.recorder import Recorder
from app.verdict import JudgeFinding, PowerWitness, SamWitness, combine, judge_finding
from contracts import VERSION
from contracts.models.card import Card, DoneWhen, Step
from contracts.models.judge import JudgeOutput, Verdict
from contracts.models.session import SessionMeta

WholeStep = "step"  # the one clause id when the judge is asked "is this step done?" with no clause list
Provider = Callable[..., Awaitable[SamWitness]]


@dataclass(frozen=True)
class Frames:
    before: StoredFrame
    now1: StoredFrame
    now2: StoredFrame
    clip: list[StoredFrame]


@dataclass(frozen=True)
class Regions:
    focus: Region | None  # the part the judge's evidence points must fall in, on NOW_1
    part: Region | None  # the witness's part, where it was last outlined
    anchor: Region | None


async def judge_and_witness(models: Models, step: Step, lang: str, fr: Frames, rg: Regions, sam_text: str,
                            provider: Provider, *, effort: str | None = None, crop: bool = True,
                            clauses: bool = True) -> tuple[judge.JudgeRun, JudgeFinding, SamWitness]:
    crop_jpeg = judge.crop(fr.now1, rg.focus) if crop else None
    inp = judge.judge_input(step if clauses else whole_step(step), lang, fr.before, fr.now1, fr.now2, crop_jpeg)
    judge_task = asyncio.ensure_future(judge.run_judge(models, inp, effort=effort))
    wit_task = asyncio.ensure_future(provider(models, fr.clip, fr.now2, wit.spec_of(step), rg.part, rg.anchor,
                                              sam_text, judge_task))
    jr = await judge_task
    sam = await wit_task
    expected = [c.id for c in step.done_when] if clauses else [WholeStep]
    return jr, judge_finding(jr.output, expected, rg.focus), sam


def whole_step(step: Step) -> Step:
    """The step with one clause, the step itself: the ablation's plain 'is this step done?'."""
    clause = DoneWhen(id=WholeStep, text=step.text, quote_ref=None)
    return step.model_copy(update={"done_when": [clause]})


async def no_witness(*_: Any) -> SamWitness:
    return SamWitness("none", None, "no second witness")


async def power_ground(models: Models, n1: StoredFrame, focus_id: str | None, region: Region | None,
                       provider: Provider, ha: HomeAssistant, alias: str | None
                       ) -> tuple[power.PowerEvidence, Region | None, Provider]:
    """A `power: off` step reads the plug and the socket on NOW_1. A step with no part (unplug): the plug and the
    socket are the part, so the judge's points must fall on them, and SAM's plug/socket finding is the second witness.
    Session.check and check_clip both call this; the caller keeps its own state (the rail, power_off_seen, HA watts)."""
    ev = await power.power_evidence(models, n1, ha, alias)
    if focus_id is None:
        region = Region(n1.frame_id, n1.width, n1.height, ev.box, source="box") if ev.box else None
        provider = power_witness(ev)
    return ev, region, provider


def power_witness(ev: power.PowerEvidence) -> Provider:
    async def provider(*_: Any) -> SamWitness:
        return SamWitness("frame", ev.camera, f"plug and socket: {ev.rail}")

    return provider


# ---- the eval's entry point ----------------------------------------------------------------------------------------


def stored(frame_id: str, t_ms: float, jpeg: bytes) -> StoredFrame:
    with Image.open(io.BytesIO(jpeg)) as im:
        w, h = im.size
    return StoredFrame(frame_id, t_ms, jpeg, hashlib.sha256(jpeg).hexdigest()[:16], w, h)


async def check_clip(card: Card, step_id: str, before: bytes, now: list[tuple[float, bytes]], *,
                     effort: str = "minimal", witness: Literal["sam", "track", "none"] = "sam", lang: str = "en",
                     lane: str = "l6", crop: bool = True, clauses: bool = True,
                     client: calls.Client | None = None) -> tuple[Verdict, JudgeOutput | None, float]:
    """The production step check on recorded frames: `now` is [(t_s, jpeg)], oldest first. Returns the verdict,
    the judge's reply (None when malformed or failed) and the dollars the calls cost."""
    step = next((s for j in card.jobs for s in j.steps if s.id == step_id), None)
    if step is None:
        raise KeyError(f"no step {step_id} in {card.brand}/{card.model}")
    if not now:
        raise ValueError("check_clip needs at least one NOW frame")
    clock_t0 = time.monotonic()

    def clock() -> float:
        return round((time.monotonic() - clock_t0) * 1000, 1)

    meta = SessionMeta(session_id=f"eval-{lane}", started_at=datetime.now(UTC), mode="api", lang=lang,
                       contracts_version=VERSION)
    models = Models(client or calls.default_client(), Recorder(None, meta), clock, household=None,
                    session_id=meta.session_id, lane=lane)
    buf = FrameBuffer(keep_s=max(config.FRAME_RING_S, (now[-1][0] - now[0][0]) + 1))
    for i, (t_s, jpeg) in enumerate(now):
        buf.add(stored(f"n{i}", t_s * 1000, jpeg))
    pair = buf.now_pair()
    assert pair is not None
    fr = Frames(stored("before", 0.0, before), pair[0], pair[1], buf.window(config.CLIP_S))
    spec = wit.spec_of(step)
    focus_id = (spec.anchor_id or spec.part_id) if spec else (step.parts[0].root if step.parts else None)
    focus = part_region = anchor = None
    if crop and focus_id:
        focus = await _outline(models, card, focus_id, fr.now1, lang)
    elif not crop:
        focus = Region.whole(fr.now1.frame_id, fr.now1.width, fr.now1.height)  # no locate: the frame is the part
    if spec is not None and crop:
        part_region = focus if spec.part_id == focus_id else None
        anchor = focus if spec.anchor_id == focus_id else None
    part = next((p for p in card.parts if spec and p.id == spec.part_id), None)
    provider: Provider = _sam_provider() if witness == "sam" else _track_provider() if witness == "track" \
        else no_witness
    pw: PowerWitness | None = None
    if step.power == "off" and focus_id is None:
        # Session.check's no-part power step (unplug): grounded on the plug and the socket, plug-out as the second
        # witness. A power-off step with a part is filmed mid-job, after the camera saw the plug out, as in a
        # session where power_off_seen is already set. No HA here: the camera alone decides, as with no entity.
        ev, grounded, power_provider = await power_ground(models, fr.now1, None, focus, provider, NoHomeAssistant(),
                                                          None)
        if crop:
            focus = grounded  # crop=False keeps the whole frame, the ablation's rung
        if witness != "none":
            provider = power_provider  # with no witness the ablation never passes, as before
        pw = ev.witness()
    t0 = time.perf_counter()
    jr, finding, sam = await judge_and_witness(models, step, lang, fr, Regions(focus, part_region, anchor),
                                               part.sam_text if part else "", provider, effort=effort, crop=crop,
                                               clauses=clauses)
    v = combine(step.id, "eval", 1, finding, sam, pw, (time.perf_counter() - t0) * 1000)
    return v, jr.output, models.usd


def _sam_provider() -> Provider:
    return wit.sam_witness


def _track_provider() -> Provider:
    """The local backend's no-SAM witness (Glimmer locate plus optical flow); its verdicts say ran='track'."""
    from local.witness import track_witness

    return track_witness


async def _outline(models: Models, card: Card, part_id: str, f: StoredFrame, lang: str) -> Region | None:
    part = next((p for p in card.parts if p.id == part_id), None)
    if part is None:
        return None
    return (await locate.outline(models, part, f, lang))[0].region
