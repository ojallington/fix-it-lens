"""SAM results to the hosted API's text grammar and Responses SSE events, so calls/ parses local and hosted SAM with
the same code. No torch here: this half is tested on any machine.

The grammar, as recorded from the hosted sam-3.1 (fixtures/l0): a frame header `<Nf>`, then per object
`id<|box;x1=;y1=;x2=;y2=;w=;h=;c=|><|mask;x=0;y=0;c=;data=H,W,PAYLOAD|>`, records joined by `,`, one line per frame.
The box is inclusive pixels in the sent image; the mask is cropped to the box, its payload the range-coded raster.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from meta_sam_parser._mask_codec import _encode_raster

MODEL = "facebook/sam3.1"
BOX_PROMPT = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")


@dataclass(frozen=True)
class Found:
    """One object on one frame. `mask` holds the box's rows of 0/1, (y2 - y1 + 1) x (x2 - x1 + 1)."""

    object_id: int
    box: tuple[int, int, int, int]  # x1, y1, x2, y2, inclusive, in image pixels
    conf: float
    mask: Sequence[Sequence[int]]


@dataclass(frozen=True)
class Prompt:
    text: str | None
    box: tuple[int, int, int, int] | None  # x1, y1, x2, y2 in image pixels


def parse_prompt(text: str) -> Prompt:
    """calls/ sends a concept as plain text, and a box as `<0f>1<|box;...|>`."""
    m = BOX_PROMPT.search(text)
    if m:
        x1, y1, x2, y2 = (int(v) for v in m.groups()[:4])
        return Prompt(None, (x1, y1, x2, y2))
    return Prompt(text.strip(), None)


def record(f: Found, width: int, height: int) -> str:
    x1, y1, x2, y2 = f.box
    w, h = x2 - x1 + 1, y2 - y1 + 1
    if len(f.mask) != h or any(len(row) != w for row in f.mask):
        raise ValueError(f"object {f.object_id}: mask is not {h}x{w}")
    payload = _encode_raster([int(v) for row in f.mask for v in row], w, h)
    c = f"{f.conf:.6g}"
    return (f"{f.object_id}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height};c={c}|>"
            f"<|mask;x=0;y=0;c={c};data={h},{w},{payload}|>")


def frames_text(frames: Sequence[Sequence[Found]], width: int, height: int) -> str:
    return "\n".join(f"<{i}f>" + ",".join(record(f, width, height) for f in objs) for i, objs in enumerate(frames))


def sse_events(text: str, *, video: bool, n_frames: int) -> list[dict[str, Any]]:
    """The event sequence the hosted API streams: content_part.done, and no output_text.done."""
    lane = {"item_id": "msg_sam_local", "output_index": 0, "content_index": 0}
    usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}
    usage["video_frames_processed" if video else "images_processed"] = n_frames
    resp = {"id": "resp_sam_local", "model": MODEL}
    evs: list[dict[str, Any]] = [
        {"type": "response.created", "response": {**resp, "status": "in_progress"}},
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"id": lane["item_id"], "type": "message", "role": "assistant", "status": "in_progress",
                  "content": []}},
        {"type": "response.content_part.added", **lane, "part": {"type": "output_text", "text": ""}},
    ]
    if text:
        evs.append({"type": "response.output_text.delta", **lane, "delta": text})
    evs.append({"type": "response.content_part.done", **lane, "part": {"type": "output_text", "text": text}})
    evs.append({"type": "response.completed", "response": {**resp, "status": "completed", "output": [],
                                                           "usage": usage}})
    return [{**ev, "sequence_number": i} for i, ev in enumerate(evs)]
