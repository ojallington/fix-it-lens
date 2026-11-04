"""The second witness: SAM 3.1 tracks the step's part over the last 2 s, in parallel with the judge.

"Done" needs the part present and stable inside its outline where it belongs (the drain cap on the drain, checked
against the drain's outline, so a cap in the hand does not count), or, for a removal step, gone from where it was.
The clip is H.264 MP4 built here with ffmpeg from frames the phone already sent (SAM rejects WebM). If the video call
is slower than the judge, it is cancelled and a single-frame SAM check on NOW_2 runs instead; the verdict says which.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import calls
from app import config
from app.frames import StoredFrame
from app.geometry import Box, Region, box_overlap
from app.models import Models
from app.verdict import Result, SamWitness
from contracts.models.card import Step

MIN_PRESENT_CONF = config.MASK_MIN_CONF
MIN_ABSENT_CONF = 0.8  # a match this sure means the part is still there
IN_PLACE = 0.5  # the part's box overlaps its anchor by at least this much
STABLE_1000 = 50  # the part's centre moves less than 5% of the frame across the clip
PRESENT_FRACTION = 0.8


@dataclass(frozen=True)
class WitnessSpec:
    part_id: str
    expect: Literal["present", "absent"]
    anchor_id: str | None


def spec_of(step: Step) -> WitnessSpec | None:
    w = step.witness
    if w is None:
        return None
    return WitnessSpec(w.part, w.expect, None if w.anchor is None else w.anchor.root)


def build_clip(frames: list[StoredFrame]) -> tuple[bytes, int, int] | None:
    """2 s of frames as H.264 MP4 (faststart, bit-exact, one thread, so a re-run builds the same bytes)."""
    if len(frames) < 2 or shutil.which("ffmpeg") is None:
        return None
    span_s = max(0.1, (frames[-1].t_ms - frames[0].t_ms) / 1000)
    fps = max(1.0, min(30.0, (len(frames) - 1) / span_s))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "clip.mp4"
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", f"{fps:.3f}", "-i", "-",
               "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
               "-pix_fmt", "yuv420p", "-threads", "1", "-movflags", "+faststart", "-fflags", "+bitexact",
               "-flags:v", "+bitexact", "-map_metadata", "-1", str(out)]
        try:
            subprocess.run(cmd, input=b"".join(f.jpeg for f in frames), check=True, timeout=10)
        except (subprocess.SubprocessError, OSError):
            return None
        w, h = frames[-1].width // 2 * 2, frames[-1].height // 2 * 2
        return out.read_bytes(), w, h


def _centre(b: Box) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def judge_tracks(res: calls.SamResult, n_frames: int, spec: WitnessSpec, anchor: Box | None) -> tuple[Result, str]:
    """From SAM's objects (per frame for video, one frame for an image) to the witness's finding."""
    tracks: dict[str, list[tuple[int, Box, float]]] = defaultdict(list)
    for o in res.objects:
        tracks[o.object_id].append((o.frame or 0, res.box_1000(o), o.conf or 0.0))
    if spec.expect == "absent":
        for obs in tracks.values():
            last = max(obs, key=lambda t: t[0])
            if last[2] >= MIN_ABSENT_CONF and (anchor is None or box_overlap(last[1], anchor) >= IN_PLACE):
                return "not_done", "the part is still in place"
        if any(o[2] >= MIN_PRESENT_CONF for obs in tracks.values() for o in obs):
            return "cant_see", "a weak match where the part was"
        return "done", "the part is gone from its place"
    if not tracks:
        return "cant_see", "SAM did not find the part"
    obs = max(tracks.values(), key=len)
    present = len({o[0] for o in obs}) / max(1, n_frames)
    last = max(obs, key=lambda t: t[0])
    if last[2] < MIN_PRESENT_CONF:
        return "cant_see", f"weak mask ({last[2]:.2f})"
    centres = [_centre(o[1]) for o in obs]
    drift = max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in centres for b in centres) if len(centres) > 1 else 0
    if anchor is not None and box_overlap(last[1], anchor) < IN_PLACE:
        return "not_done", "the part is not where it belongs"
    if n_frames > 1 and (present < PRESENT_FRACTION or drift > STABLE_1000):
        return "not_done", "the part is moving"
    return "done", "the part is present, stable and in place"


def _prompt(spec: WitnessSpec, part: Region | None, sam_text: str) -> str | calls.Box:
    if spec.expect == "present" and part is not None:
        return part.box
    return sam_text


async def sam_witness(models: Models, clip_frames: list[StoredFrame], now2: StoredFrame, spec: WitnessSpec | None,
                      part: Region | None, anchor: Region | None, sam_text: str,
                      judge_done: asyncio.Future[Any]) -> SamWitness:
    if spec is None:
        return SamWitness("none", None, "this step names no part to witness")
    anchor_box = anchor.box if anchor is not None else (part.box if part is not None else None)
    prompt = _prompt(spec, part, sam_text)
    clip = build_clip(clip_frames) if not models.rerunning else None
    if models.rerunning:
        ran = models.pick("witness", None)
        if ran == "video":
            clip = build_clip(clip_frames)
        elif ran == "frame":
            models.skip("sam_video")  # the live run started the video call and dropped it
    if clip is not None:
        data, w, h = clip
        video = asyncio.ensure_future(models.sam_video(data, prompt, width=w, height=h, frames=len(clip_frames),
                                                       timeout_s=config.CALL_TIMEOUT_S))
        if not models.rerunning:
            await asyncio.wait({video, judge_done}, return_when=asyncio.FIRST_COMPLETED)
        if models.rerunning or video.done():
            try:
                res, _ = await video
                if not models.rerunning:
                    models.pick("witness", "video")
                result, why = judge_tracks(res, len(clip_frames), spec, anchor_box)
                return SamWitness("video", result, why)
            except calls.CallError:
                pass
        else:
            video.cancel()
            with contextlib.suppress(asyncio.CancelledError, calls.CallError):
                await video
        if not models.rerunning:
            models.pick("witness", "frame")
    try:
        res, _ = await models.sam_image(now2, prompt)
    except calls.CallError as e:
        return SamWitness("none", None, f"SAM failed: {type(e).__name__}")
    result, why = judge_tracks(res, 1, spec, anchor_box)
    return SamWitness("frame", result, why)
