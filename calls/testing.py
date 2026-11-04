"""Synthetic fixtures for tests: build the event streams a recorded call would hold, and write them where replay
looks for them. Tests use these when no day-0 recording covers the case (a wrong model, an incomplete reply).

    client = calls.make_client("replay", fixtures_root=tmp, private=None)
    body = client.build_respond_body("judge", "x", effort="minimal")
    write_respond_fixture(tmp, body, responses_events(text='{"clauses": []}'))
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meta_sam_parser._mask_codec import _encode_raster  # test-only: a canonical one_bit payload

from calls.fixtures import request_sha256, write_fixture

Event = tuple[float, dict[str, Any]]


def responses_events(
    *, text: str = "", model: str = "muse-spark-1.3", status: str = "completed",
    function_calls: Sequence[tuple[str, str, dict[str, Any]]] = (), input_tokens: int = 1000,
    output_tokens: int = 200, reasoning_tokens: int = 150, latency_ms: float = 900.0,
) -> list[Event]:
    """A Responses stream: created, deltas, then completed or incomplete."""
    output: list[dict[str, Any]] = [{"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "enc"}]
    events: list[Event] = [(latency_ms * 0.3, {"type": "response.created", "sequence_number": 0,
                                               "response": {"id": "resp_1", "model": model, "status": "in_progress"}})]
    seq = 1
    if status == "completed":
        for i, (name, call_id, args) in enumerate(function_calls):
            item = {"id": f"fc_{i}", "type": "function_call", "name": name, "call_id": call_id,
                    "arguments": json.dumps(args), "status": "completed"}
            events.append((latency_ms * 0.8, {"type": "response.function_call_arguments.delta", "sequence_number": seq,
                                              "item_id": item["id"], "output_index": i + 1,
                                              "delta": item["arguments"]}))
            seq += 1
            output.append(item)
        if text:
            events.append((latency_ms * 0.8, {"type": "response.output_text.delta", "sequence_number": seq,
                                              "item_id": "msg_1", "output_index": len(output), "content_index": 0,
                                              "delta": text}))
            seq += 1
            output.append({"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": text, "annotations": []}]})
    final: dict[str, Any] = {
        "id": "resp_1", "model": model, "status": status, "output": output,
        "usage": {"input_tokens": input_tokens, "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens": output_tokens, "output_tokens_details": {"reasoning_tokens": reasoning_tokens}},
    }
    if status == "incomplete":
        final["incomplete_details"] = {"reason": "max_output_tokens"}
    events.append((latency_ms, {"type": f"response.{status}", "sequence_number": seq, "response": final}))
    return events


@dataclass(frozen=True)
class FakeObject:
    object_id: int
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 inclusive, in image pixels
    conf: float = 0.9


def sam_text(objects: Sequence[FakeObject], width: int, height: int) -> str:
    parts = []
    for o in objects:
        x1, y1, x2, y2 = o.box
        w, h = x2 - x1 + 1, y2 - y1 + 1
        payload = _encode_raster([1] * (w * h), w, h)
        parts.append(f"{o.object_id}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height};c={o.conf}|>"
                     f"<|mask;x=0;y=0;data={h},{w},{payload}|>")
    return "<0f>" + ",".join(parts) if parts else ""


def sam_events(objects: Sequence[FakeObject], width: int, height: int, *, model: str = "sam-3.1",
               latency_ms: float = 800.0) -> list[Event]:
    """A SAM image stream as the API sends it: content_part.done, but no output_text.done."""
    text = sam_text(objects, width, height)
    lane = {"item_id": "msg_1", "output_index": 0, "content_index": 0}
    events: list[Event] = [
        (latency_ms * 0.5, {"type": "response.created", "sequence_number": 0,
                            "response": {"id": "resp_s", "model": model, "status": "in_progress"}}),
        (latency_ms * 0.6, {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0,
                            "item": {"id": "msg_1", "type": "message", "role": "assistant", "status": "in_progress",
                                     "content": []}}),
        (latency_ms * 0.6, {"type": "response.content_part.added", "sequence_number": 2, **lane,
                            "part": {"type": "output_text", "text": ""}}),
    ]
    if text:
        events.append((latency_ms * 0.9, {"type": "response.output_text.delta", "sequence_number": 3, **lane,
                                          "delta": text}))
    events.append((latency_ms * 0.95, {"type": "response.content_part.done", "sequence_number": 4, **lane,
                                       "part": {"type": "output_text", "text": text}}))
    events.append((latency_ms, {"type": "response.completed", "sequence_number": 5, "response": {
        "id": "resp_s", "model": model, "status": "completed", "output": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "images_processed": 1}}}))
    return events


def write_respond_fixture(root: Path, body: dict[str, Any], events: list[Event], *, lane: str = "test",
                          kind: str = "respond") -> str:
    sha = request_sha256(body)
    write_fixture(root, lane, sha, kind=kind, role="test", params={"model": body["model"], "store": False},
                  events=events, response_model=body["model"], latency_ms=events[-1][0], usd=0.0)
    return sha
