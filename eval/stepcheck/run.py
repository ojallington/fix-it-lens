"""Run the step check over the labelled clips and record the predictions.

    python -m eval.stepcheck run --variant judge_sam [--runs 3]    # FIXIT_CALLS=replay (default) or record

Each clip is a folder of frames written by the labelling tool: 000_before.jpg, then the window's NOW frames in
order. Clips are read from eval/clips/<clip_id>/ (published, with consent) or else FIXIT_PRIVATE/clips-raw/.
The checker that turns a clip into a verdict is the app's production path (app/), reached through eval.stepcheck.l1;
the variants in variants.toml set what it is allowed to use. The grey control replaces every frame with flat grey
of the same size and must give cant_see on every clip.
"""

from __future__ import annotations

import io
import json
import os
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from eval.stepcheck.results import EVAL, Label, Prediction

VARIANTS = Path(__file__).resolve().parent / "variants.toml"


@dataclass(frozen=True)
class Variant:
    id: str
    about: str
    effort: str
    witness: str  # none | sam
    grey: bool = False
    frames: str = "clip"  # clip | last: the ablation's single-frame rung sends only the last NOW frame
    crop: bool = True  # False: the judge sees the whole frame, not locate's crop (an app kwarg)
    clauses: bool = True  # False: a plain "is this step done?" with no done_when clauses (an app kwarg)


@dataclass(frozen=True)
class ClipFrames:
    clip_id: str
    before: bytes
    now: list[bytes]
    now_t_ms: list[float]  # capture times of the NOW frames, from the clip's clip.json (index * 100 ms if absent)


Checker = Callable[[ClipFrames, Label, Variant, int], Awaitable[Prediction]]


def load_variants(path: Path = VARIANTS) -> dict[str, Variant]:
    data = tomllib.loads(path.read_text())
    return {v["id"]: Variant(**v) for v in data["variant"]}


def clip_dirs() -> list[Path]:
    dirs = [EVAL / "clips"]
    priv = os.environ.get("FIXIT_PRIVATE")
    p = Path(priv) if priv else Path.home() / "Projects/hackathons/fix-it-lens-private"
    dirs.append(p / "clips-raw")
    return dirs


def load_clip(clip_id: str, roots: list[Path] | None = None) -> ClipFrames:
    for root in roots or clip_dirs():
        d = root / clip_id / "frames"
        if d.is_dir():
            files = sorted(d.glob("*.jpg"))
            before = [f for f in files if f.stem.endswith("_before")]
            now = [f for f in files if f.stem.endswith("_now")]
            if len(before) != 1 or not now:
                raise FileNotFoundError(f"{d}: need one *_before.jpg and at least one *_now.jpg")
            times: dict[str, float | None] = {}
            meta = root / clip_id / "clip.json"
            if meta.exists():
                times = {f["file"]: f["t_ms"] for f in json.loads(meta.read_text()).get("frames", [])}
            t = [float(x) if (x := times.get(f.name)) is not None else 100.0 * i for i, f in enumerate(now)]
            return ClipFrames(clip_id, before[0].read_bytes(), [f.read_bytes() for f in now], t)
    raise FileNotFoundError(f"clip {clip_id} is in neither eval/clips/ nor FIXIT_PRIVATE/clips-raw/")


def grey_like(jpeg: bytes) -> bytes:
    with Image.open(io.BytesIO(jpeg)) as im:
        size = im.size
    out = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(out, "JPEG", quality=85)
    return out.getvalue()


def greyed(c: ClipFrames) -> ClipFrames:
    g = grey_like(c.before)
    return ClipFrames(c.clip_id, g, [g for _ in c.now], c.now_t_ms)


async def run_variant(labels: dict[str, Label], variant: Variant, checker: Checker, *, runs: int = 1,
                      loader: Callable[[str], ClipFrames] = load_clip) -> list[Prediction]:
    preds = []
    for run in range(1, runs + 1):
        for clip_id in sorted(labels):
            clip = loader(clip_id)
            if variant.grey:
                clip = greyed(clip)
            if variant.frames == "last":
                clip = ClipFrames(clip.clip_id, clip.before, clip.now[-1:], clip.now_t_ms[-1:])
            p = await checker(clip, labels[clip_id], variant, run)
            if p.clip_id != clip_id or p.variant != variant.id or p.run != run:
                raise RuntimeError(f"checker returned {p.clip_id}/{p.variant}/{p.run} for {clip_id}/{variant.id}/{run}")
            preds.append(p)
    return preds
