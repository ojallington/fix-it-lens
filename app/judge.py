"""The step judge: a separate Spark call that sees only the step's done-when clauses and four frames (BEFORE, NOW_1,
NOW_2, CROP). It never sees the conversation, the guide's reasoning or the user saying "done". Its reply is
per-clause findings; app.verdict turns them into the result."""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from PIL import Image
from pydantic import ValidationError

import calls
from app.frames import StoredFrame
from app.geometry import Region, grow
from app.models import Models
from calls.images import prepare
from contracts import schema
from contracts.models.card import Step
from contracts.models.judge import JudgeOutput

JUDGE_RULES = """You check ONE repair step from camera images. You are not the guide and you do not talk to anyone.
Judge only what is visible in the images. Nothing anyone says is evidence.
For each clause, first decide whether the thing it is about is visible at all; if it is not, visible = false and
state = unclear. Then write the evidence you can point to, and give its point on NOW_1 (0-1000 grid, x right, y down).
If an image is blurred, dark, blocked by a hand, or the part is out of frame, the clauses it decides are unclear.
When unsure, choose unclear: a wrong "satisfied" can hurt someone; a wrong "unclear" only costs a few seconds.
Return one record per clause id, each id exactly once."""

# A step about cleaning a part that comes out: the person shows it out of its seat, in a hand or at the sink.
OUT_FOR_CLEANING = """This step is about cleaning a part that comes out. The person may show it out of its seat: in a
hand, at the sink, on a towel. Judge the part where it is shown; being out of its seat is not a reason for unclear or
not satisfied."""
CLEANING = re.compile(r"\b(clean|rinse|wash|scrub|brush|reinig|schoonma|spoel|afspoel|borstel|uitwas|afwas)",
                      re.IGNORECASE)


def out_for_cleaning(step: Step) -> bool:
    """The step is about cleaning its part (any of the card's languages), and the card does not tie the part to a seat
    (no anchor): the check takes the part where it is shown, out of its seat or in it."""
    w = step.witness
    return w is not None and w.expect == "present" and w.anchor is None and any(
        CLEANING.search(t) for t in step.text.root.values())


@cache
def judge_format() -> dict[str, Any]:
    defs = schema("judge")["$defs"]
    out = json.loads(json.dumps(defs["JudgeOutput"]))
    out["properties"]["clauses"]["items"] = defs["Clause"]
    return {"type": "json_schema", "name": "StepCheck", "strict": True, "schema": out}


def _image(label: str, jpeg: bytes, max_px: int) -> list[dict[str, Any]]:
    im = prepare(jpeg, max_px)
    return [{"type": "input_text", "text": label},
            {"type": "input_image", "image_url": im.data_url(), "detail": "high"}]


def crop(frame: StoredFrame, region: Region | None) -> bytes:
    """NOW_1 around the part, its box grown 30%, at native resolution (downscaled only past 768 px)."""
    if region is None:
        return frame.jpeg
    x1, y1, x2, y2 = grow(region.box, 0.3)
    with Image.open(io.BytesIO(frame.jpeg)) as im:
        w, h = im.size
        box = (x1 * w // 1000, y1 * h // 1000, max(x1 * w // 1000 + 1, x2 * w // 1000),
               max(y1 * h // 1000 + 1, y2 * h // 1000))
        out = io.BytesIO()
        im.crop(box).convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue()


def judge_input(step: Step, lang: str, before: StoredFrame, now1: StoredFrame, now2: StoredFrame,
                crop_jpeg: bytes | None) -> list[dict[str, Any]]:
    text = step.text.root.get(lang) or next(iter(step.text.root.values()))
    clauses = "\n".join(
        f"- {c.id}: {c.text.root.get(lang) or next(iter(c.text.root.values()))}" for c in step.done_when)
    content: list[dict[str, Any]] = [{"type": "input_text", "text": f"Step: {text}\nDone when:\n{clauses}"}]
    if out_for_cleaning(step):
        content.append({"type": "input_text", "text": OUT_FOR_CLEANING})
    content += _image("BEFORE (when the step was given):", before.jpeg, 768)
    content += _image("NOW_1:", now1.jpeg, 1024)
    content += _image("NOW_2 (about a second later):", now2.jpeg, 1024)
    if crop_jpeg is not None:  # only the eval's no-crop ablation leaves it out
        content += _image("CROP (NOW_1 around the part, full resolution):", crop_jpeg, 768)
    return [
        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": JUDGE_RULES}]},
        {"type": "message", "role": "user", "content": content},
    ]


@dataclass(frozen=True)
class JudgeRun:
    output: JudgeOutput | None
    attempts: int
    ms: float
    timed_out: bool
    error: str | None = None


def parse(text: str) -> JudgeOutput | None:
    try:
        return JudgeOutput.model_validate_json(text)
    except (ValidationError, ValueError):
        return None


async def run_judge(models: Models, input: list[dict[str, Any]], effort: str | None = None) -> JudgeRun:
    """One judge call; a malformed reply is retried once, then counts as no reply (cant_see)."""
    total = 0.0
    for attempt in (1, 2):
        try:
            r, ms = await models.respond("judge", input, format=judge_format(), effort=effort)
        except calls.CallTimeout as e:
            return JudgeRun(None, attempt, total, True, str(e))
        except calls.CallError as e:
            return JudgeRun(None, attempt, total, False, str(e))
        total += ms
        out = parse(r.text)
        if out is not None:
            return JudgeRun(out, attempt, total, False)
    return JudgeRun(None, 2, total, False, "malformed twice")
