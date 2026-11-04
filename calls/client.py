"""The one model client. Every call, in every mode:

- streams (a non-streaming request hits a 504) and sends `store: false`;
- downscales images to 1024 px;
- checks the ledger before any network I/O, and writes one ledger line per attempt;
- asserts `response.model` against the pinned ID;
- sends `max_output_tokens` of at least 4000 (8000 at `medium` or higher), retries an `incomplete` reply once at
  double the cap, and never parses an incomplete reply;
- times out per role (6 s for the live roles).

Modes: `live`; `record` (live, and the response is written to fixtures/<lane>/<request_sha256>.jsonl);
`replay` (from fixtures by request hash; no key; a missing fixture raises MissingFixture). CI runs replay only.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import io
import json
import re
import time
import uuid
import wave
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from meta_sam_parser import (
    ImageSegmentationResult,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    VideoSegmentationResult,
    decode_mask_to_svg_path,
    image_segmentation_format,
    parse_responses_stream,
    video_segmentation_format,
)

from calls import local_prompts, pricing
from calls.backends import Backend
from calls.config import Config, RoleConfig, check_model_id, output_floor
from calls.errors import (
    CallFailed,
    CallTimeout,
    IncompleteResponse,
    ModelMismatch,
)
from calls.fixtures import request_sha256, save_request_body, write_fixture
from calls.images import PreparedImage, prepare
from calls.ledger import Ledger

Mode = Literal["live", "record", "replay"]
Box = tuple[int, int, int, int]  # x_min, y_min, x_max, y_max on the 0-1000 grid
FINAL_EVENTS = {"response.completed", "response.incomplete", "response.failed", "error"}
SAM_CACHE_SIZE = 512


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    images_processed: int | None = None
    video_frames_processed: int | None = None

    @classmethod
    def from_response(cls, resp: dict[str, Any]) -> Usage:
        u = resp.get("usage") or {}
        return cls(
            input_tokens=int(u.get("input_tokens") or 0),
            cached_tokens=int((u.get("input_tokens_details") or {}).get("cached_tokens") or 0),
            output_tokens=int(u.get("output_tokens") or 0),
            reasoning_tokens=int((u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0),
            images_processed=u.get("images_processed"),
            video_frames_processed=u.get("video_frames_processed"),
        )


@dataclass(frozen=True)
class FunctionCall:
    name: str
    call_id: str
    arguments: str

    def args(self) -> Any:
        """The raw arguments, JSON-decoded. Not schema-guaranteed: validate them with the contracts models."""
        return json.loads(self.arguments) if self.arguments else {}


@dataclass(frozen=True)
class Response:
    role: str
    model: str
    status: str
    text: str
    output: list[dict[str, Any]]
    function_calls: list[FunctionCall]
    usage: Usage
    latency_ms: float
    ttft_ms: float | None
    usd: float
    request_sha256: str
    attempts: int
    call_id: str
    reasoning_summary: str = ""  # the reasoning items' summary text, when the role asks for one; often empty
    delta_error: str | None = None  # respond(on_delta=...) raised: why, and it was not called again that attempt

    def parsed(self) -> Any:
        return json.loads(self.text)


@dataclass(frozen=True)
class SamObject:
    object_id: str
    box_px: tuple[float, float, float, float]  # left, top, right, bottom in the sent image's pixels
    conf: float | None
    mask_svg: str | None  # SVG path in the sent image's pixels
    frame: int | None = None


@dataclass(frozen=True)
class SamResult:
    width: int
    height: int
    objects: list[SamObject]
    raw_output: str
    latency_ms: float
    usd: float
    request_sha256: str
    cached: bool = False

    def box_1000(self, obj: SamObject) -> Box:
        left, top, right, bottom = obj.box_px
        sx, sy = 1000 / self.width, 1000 / self.height
        return (round(left * sx), round(top * sy), min(1000, round(right * sx)), min(1000, round(bottom * sy)))


@dataclass(frozen=True)
class Transcript:
    text: str
    body: dict[str, Any]
    latency_ms: float
    usd: float
    request_sha256: str



def _delta_at(on_delta: Callable[[str, int], None], attempt: int, text: str) -> None:
    on_delta(text, attempt)

@dataclass
class _Stream:
    events: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    final: dict[str, Any] = field(default_factory=dict)
    final_type: str = ""
    ttft_ms: float | None = None
    latency_ms: float = 0.0
    delta_error: str | None = None   # the on_delta callback raised; it was not called again for this stream


def _images_message(text: str | None, images: Sequence[PreparedImage]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "input_text", "text": text})
    content += [{"type": "input_image", "image_url": im.data_url(), "detail": "high"} for im in images]
    return {"type": "message", "role": "user", "content": content}


def _text_chars(obj: Any) -> int:
    if isinstance(obj, str):
        return 0 if obj.startswith("data:") else len(obj)
    if isinstance(obj, dict):
        return sum(_text_chars(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_text_chars(v) for v in obj)
    return 0


_PAIR = re.compile(r"(-?\d+(?:\.\d+)?)[ ,](-?\d+(?:\.\d+)?)")


def _translate_path(path: str, dx: float, dy: float) -> str:
    def shift(m: re.Match[str]) -> str:
        x, y = float(m.group(1)) + dx, float(m.group(2)) + dy
        return f"{x:g} {y:g}"

    return _PAIR.sub(shift, path)


async def _sam_events(events: list[tuple[float, dict[str, Any]]]) -> AsyncIterator[dict[str, Any]]:
    """Feed recorded events to the parser. The API sends response.content_part.done but not
    response.output_text.done, which the parser needs to close a lane, so it is synthesized here."""
    done: set[tuple[Any, Any, Any]] = set()
    for _, ev in events:
        lane = (ev.get("item_id"), ev.get("output_index"), ev.get("content_index"))
        if ev.get("type") == "response.output_text.done":
            if lane not in done:
                done.add(lane)
                yield ev
            continue
        part = ev.get("part")
        if (ev.get("type") == "response.content_part.done" and lane not in done and isinstance(part, dict)
                and part.get("type") == "output_text"):
            yield ev
            done.add(lane)
            yield {"type": "response.output_text.done", "item_id": ev.get("item_id"),
                   "output_index": ev.get("output_index"), "content_index": ev.get("content_index"),
                   "text": part.get("text", ""), "sequence_number": ev.get("sequence_number", 0)}
            continue
        yield ev


_RUN: ContextVar[str | None] = ContextVar("calls_run", default=None)


@contextmanager
def run_tag(run: str) -> Iterator[None]:
    """Tag every ledger line written inside this scope with `run` (for cost rows), unless the line has its own.

    A contextvar, so it follows awaits and the tasks started inside the scope; an inner scope wins.
    """
    token = _RUN.set(run)
    try:
        yield
    finally:
        _RUN.reset(token)


class Client:
    def __init__(
        self,
        mode: Mode,
        config: Config,
        backend: Backend,
        *,
        ledger: Ledger | None,
        fixtures_root: Path,
        private: Path | None,
    ) -> None:
        for role in config.roles.values():
            check_model_id(role.model)
        if mode in ("live", "record") and ledger is None:
            raise CallFailed("live and record modes need the ledger")
        self.mode = mode
        self.config = config
        self.backend = backend
        self.ledger = ledger
        self.fixtures_root = fixtures_root
        self.private = private
        self._sam_cache: OrderedDict[tuple[str, str, str], SamResult] = OrderedDict()
        self.router: Any = None  # calls.routing.Router: public sessions' partner-role turns, when routing is on
        self.requests: list[dict[str, Any]] = []  # every body sent this process (tests read it)
        self.ledger_mode = {"local": "local", "partner": "partner"}.get(backend.name, "api")
        # who served the call, for the ledger and the credit pools: meta, a partner, or local
        self.provider = getattr(backend, "provider", None) or ("local" if self.ledger_mode == "local" else "meta")

    # ---- the policy shared by every call -------------------------------------------------------------------

    def _prepare(self, images: Sequence[bytes]) -> list[PreparedImage]:
        return [prepare(b, self.config.max_px, self.config.jpeg_quality) for b in images]

    async def _stream(self, body: dict[str, Any], sha: str, kind: str, timeout_s: float,
                      on_delta: Callable[[str], None] | None = None) -> _Stream:
        out = _Stream()
        t0 = time.perf_counter()
        try:
            async with asyncio.timeout(timeout_s):
                async for t_ms, ev in self.backend.stream(body, sha=sha, kind=kind):
                    out.events.append((t_ms, ev))
                    etype = str(ev.get("type", ""))
                    if out.ttft_ms is None and etype in ("response.output_text.delta",
                                                         "response.function_call_arguments.delta"):
                        out.ttft_ms = t_ms
                    if on_delta is not None and etype == "response.output_text.delta" and out.delta_error is None:
                        try:
                            on_delta(str(ev.get("delta", "")))
                        except Exception as e:  # a preview must never cost the paid call it watches
                            out.delta_error = f"{type(e).__name__}: {e}"
                    if etype in FINAL_EVENTS:
                        out.final_type = etype
                        out.final = ev.get("response") or ev
        except TimeoutError:
            raise CallTimeout(f"{kind} passed its {timeout_s:g} s timeout") from None
        out.latency_ms = out.events[-1][0] if out.events else (time.perf_counter() - t0) * 1000
        return out

    def _ledger_line(self, **row: Any) -> None:
        if self.ledger is not None and self.mode != "replay":
            if not row.get("run"):
                row["run"] = _RUN.get()
            self.ledger.append(row)

    def _check(self, lane: str, estimate: float, household: str | None = None, cap: float | None = None) -> None:
        if self.ledger is not None:
            self.ledger.check(lane, estimate, household=household, household_cap_usd=cap)

    def _record(self, lane: str, sha: str, kind: str, role: RoleConfig, params: dict[str, Any],
                events: list[tuple[float, dict[str, Any]]], response_model: str | None, latency_ms: float,
                usd: float, http_status: int | None = 200) -> None:
        if self.mode == "record":
            write_fixture(self.fixtures_root, lane, sha, kind=kind, role=role.name, params=params, events=events,
                          response_model=response_model, latency_ms=latency_ms, usd=usd, http_status=http_status)

    def _assert_model(self, role: RoleConfig, got: str | None) -> None:
        if got != role.model:
            raise ModelMismatch(f"{role.name}: response.model {got!r} is not the pinned {role.model!r}")

    async def _responses_call(
        self, *, kind: str, role: RoleConfig, body: dict[str, Any], params: dict[str, Any], lane: str,
        household: str | None, session_id: str | None, estimate: float, timeout_s: float,
        household_cap_usd: float | None = None, on_delta: Callable[[str], None] | None = None,
        origin: str | None = None,
    ) -> tuple[_Stream, str, float, str]:
        assert body["store"] is False and body["stream"] is True
        sha = request_sha256(body)
        call_id = uuid.uuid4().hex[:12]
        local = self.ledger_mode == "local"  # Glimmer and SAM on the LAN: nothing billed
        if local:
            estimate = 0.0
        self._check(lane, estimate, household, household_cap_usd)
        self.requests.append(body)
        if self.mode == "record":
            save_request_body(self.private, sha, body)
        base = {"role": role.name, "call_id": call_id, "request_sha256": sha, "model": role.model, "lane": lane,
                "household": household, "session_id": session_id, "mode": self.ledger_mode,
                "provider": self.provider, "origin": origin, "probe": role.name}
        try:
            s = await self._stream(body, sha, kind, timeout_s, on_delta)
        except CallTimeout as e:
            self._ledger_line(**base, status="timeout", usd=estimate, error=f"{e} (usd is the estimate)")
            e.usd = estimate
            raise
        resp = s.final
        usage = Usage.from_response(resp)
        if kind == "respond":
            usd = pricing.token_usd(role.model, usage.input_tokens, usage.cached_tokens, usage.output_tokens)
        elif kind == "sam_video":
            usd = pricing.sam_video_usd(usage.video_frames_processed or 0)
        else:
            usd = (usage.images_processed or 1) * pricing.SAM_IMAGE_USD
        if local:
            usd = 0.0
        status = str(resp.get("status") or s.final_type.removeprefix("response.") or "missing")
        response_model = resp.get("model")
        incomplete = (resp.get("incomplete_details") or {}).get("reason")
        error = resp.get("error")
        self._ledger_line(
            **base, response_model=response_model, status=status, incomplete=incomplete,
            latency_s=round(s.latency_ms / 1000, 3),
            ttft_s=None if s.ttft_ms is None else round(s.ttft_ms / 1000, 3),
            first_event_s=round(s.events[0][0] / 1000, 3) if s.events else None,
            input_tokens=usage.input_tokens, cached_tokens=usage.cached_tokens, output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens, images_processed=usage.images_processed,
            video_frames_processed=usage.video_frames_processed, usd=round(usd, 6),
            error=json.dumps(error) if error else None,
        )
        self._record(lane, sha, kind, role, params, s.events, response_model, s.latency_ms, usd)
        if s.final_type == "error" or status == "failed" or not s.final:
            raise CallFailed(f"{role.name}: {status}: {json.dumps(error or s.final)[:300]}")
        self._assert_model(role, response_model)
        return s, status, usd, call_id

    # ---- Spark / Wonder ------------------------------------------------------------------------------------

    def build_respond_body(
        self, role: str, input: str | list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
        format: dict[str, Any] | None = None, effort: str | None = None, images: Sequence[bytes] = (),
        instructions: str | None = None, max_output_tokens: int | None = None, attempt: int = 1,
    ) -> dict[str, Any]:
        """The exact request body respond() sends (replay keys on its hash)."""
        r = self.config.role(role)
        eff = effort or r.effort or "low"
        cap = max(max_output_tokens or r.max_output_tokens or 0, output_floor(eff)) * (2 if attempt > 1 else 1)
        prepared = self._prepare(images)
        items: list[dict[str, Any]]
        if isinstance(input, str):
            items = [_images_message(input, prepared)]
        else:
            items = list(input) + ([_images_message(None, prepared)] if prepared else [])
        if self.config.prompts_dir is not None:
            instructions, items = local_prompts.apply(self.config.prompts_dir, role, instructions, items)
        body: dict[str, Any] = {
            "model": r.model, "stream": True, "store": False, "input": items,
            "reasoning": {"effort": eff}, "max_output_tokens": cap,
        }
        if r.reasoning_summary:
            body["reasoning"]["summary"] = r.reasoning_summary
        if instructions:
            body["instructions"] = instructions
        if r.include_reasoning:
            body["include"] = ["reasoning.encrypted_content"]
        if r.prompt_cache_key:
            body["prompt_cache_key"] = r.prompt_cache_key
        if tools:
            body["tools"] = tools
            body["parallel_tool_calls"] = True
        if format:
            body["text"] = {"format": format}
        return body

    async def respond(
        self, role: str, input: str | list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
        format: dict[str, Any] | None = None, effort: str | None = None, images: Sequence[bytes] = (),
        timeout_s: float | None = None, lane: str, household: str | None = None, session_id: str | None = None,
        household_cap_usd: float | None = None,
        instructions: str | None = None, max_output_tokens: int | None = None, origin: str | None = None,
        on_delta: Callable[[str, int], None] | None = None,
    ) -> Response:
        """One Responses call. `format` is a text.format value ({type: json_schema, name, schema, strict}).
        `images` are appended as one user message (downscaled to 1024 px). Retries `incomplete` once at double
        the cap; a second `incomplete` raises IncompleteResponse and is never parsed.

        `on_delta(text, attempt)` sees each output_text delta as it streams, in every mode (replay plays the
        fixture's deltas). A retry starts a new stream with attempt 2, so drop what attempt 1 sent. It is a
        preview of unchecked model text: nothing in it is a result. If it raises, it is not called again for that
        attempt, the call goes on, and Response.delta_error says why. Bodies, the ledger and the sha are unchanged.
        """
        if self.router is not None and origin is not None and role in self.config.partner_roles:
            routed: Response = await self.router.respond(
                role, input, origin=origin, session_id=session_id, tools=tools, format=format, effort=effort,
                images=images, timeout_s=timeout_s, lane=lane, household=household,
                household_cap_usd=household_cap_usd, instructions=instructions, max_output_tokens=max_output_tokens)
            return routed
        r = self.config.role(role)
        timeout = timeout_s if timeout_s is not None else r.timeout_s
        for attempt in (1, 2):
            body = self.build_respond_body(role, input, tools=tools, format=format, effort=effort, images=images,
                                           instructions=instructions, max_output_tokens=max_output_tokens,
                                           attempt=attempt)
            pixels = [im.pixels for im in self._prepare(images)]
            estimate = pricing.estimate_respond_usd(r.model, _text_chars(body), pixels, body["max_output_tokens"])
            params = {
                "model": r.model, "store": False, "stream": True, "effort": body["reasoning"]["effort"],
                "max_output_tokens": body["max_output_tokens"],
                "format_name": (format or {}).get("name"), "tool_names": [t.get("name", "") for t in tools or []],
                "n_images": len(pixels), "image_px": self.config.max_px if pixels else None,
                "prompt_cache_key": body.get("prompt_cache_key"), "attempt": attempt,
                "reasoning_summary": body["reasoning"].get("summary"),
            }
            s, status, usd, call_id = await self._responses_call(
                kind="respond", role=r, body=body, params=params, lane=lane, household=household,
                session_id=session_id, household_cap_usd=household_cap_usd, estimate=estimate, timeout_s=timeout,
                on_delta=None if on_delta is None else functools.partial(_delta_at, on_delta, attempt),
                origin=origin,
            )
            if status == "incomplete":
                if attempt == 1:
                    continue
                raise IncompleteResponse(f"{role}: incomplete twice (cap {body['max_output_tokens']}); not parsed")
            resp = s.final
            output = list(resp.get("output") or [])
            text = "".join(
                c.get("text", "") for item in output if item.get("type") == "message"
                for c in item.get("content") or [] if c.get("type") == "output_text"
            )
            summary = "\n".join(
                str(p.get("text", "")) for item in output if item.get("type") == "reasoning"
                for p in item.get("summary") or [] if p.get("text")
            )
            calls = [FunctionCall(str(i.get("name")), str(i.get("call_id")), str(i.get("arguments") or ""))
                     for i in output if i.get("type") == "function_call"]
            return Response(role=role, model=r.model, status=status, text=text, output=output, function_calls=calls,
                            usage=Usage.from_response(resp), latency_ms=s.latency_ms, ttft_ms=s.ttft_ms, usd=usd,
                            request_sha256=request_sha256(body), attempts=attempt, call_id=call_id,
                            reasoning_summary=summary, delta_error=s.delta_error)
        raise AssertionError("unreachable")  # pragma: no cover

    async def count_tokens(
        self, role: str, input: str | list[dict[str, Any]], *, images: Sequence[bytes] = (), lane: str,
        instructions: str | None = None, tools: list[dict[str, Any]] | None = None,
    ) -> int:
        """Input tokens for the body respond() would send (POST /v1/responses/input_tokens: no generation
        usage, $0, but still an API call: the ledger logs it and replay keys on it)."""
        full = self.build_respond_body(role, input, images=images, instructions=instructions, tools=tools)
        body: dict[str, Any] = {k: full[k] for k in ("model", "input", "instructions", "tools") if k in full}
        sha = request_sha256(body)
        r = self.config.role(role)
        self._check(lane, 0.0)
        self.requests.append(body)
        if self.mode == "record":
            save_request_body(self.private, sha, body)
        latency, status, data = await self.backend.count_tokens(body, sha=sha)
        self._ledger_line(role=r.name, request_sha256=sha, model=r.model, lane=lane, mode=self.ledger_mode,
                          provider=self.provider,
                          probe="input_tokens", status="completed" if status == 200 else f"http_{status}",
                          input_tokens=data.get("input_tokens"), latency_s=round(latency / 1000, 3), usd=0.0,
                          error=None if status == 200 else json.dumps(data)[:300])
        params = {"model": r.model, "store": False, "stream": False, "n_images": len(images),
                  "image_px": self.config.max_px if images else None}
        self._record(lane, sha, "count_tokens", r, params, [(latency, {"type": "input_tokens.response",
                                                                       "body": data})],
                     None, latency, 0.0, http_status=status)
        if status != 200 or not isinstance(data.get("input_tokens"), int):
            raise CallFailed(f"input_tokens: HTTP {status}: {json.dumps(data)[:300]}")
        return int(data["input_tokens"])

    # ---- SAM 3.1 -------------------------------------------------------------------------------------------

    def _sam_prompt(self, prompt: str | Box, width: int, height: int) -> str:
        if isinstance(prompt, str):
            if not prompt.strip() or len(prompt) > 60:
                raise ValueError("a SAM concept is one short noun phrase")
            return prompt
        x1, y1, x2, y2 = (round(v * s / 1000) for v, s in zip(prompt, (width, height, width, height), strict=True))
        x2, y2 = min(max(x2, x1 + 1), width - 1), min(max(y2, y1 + 1), height - 1)
        return f"<0f>1<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height}|>"

    def _sam_body(self, role: RoleConfig, text: str, media: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": role.model, "stream": True, "store": False,
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}, media]}],
            "metadata": {"mask_encoding": role.mask_encoding or "one_bit", "include_confidence": "true"},
        }

    async def _parse_sam(self, s: _Stream, video: bool) -> tuple[list[SamObject], str]:
        result: ImageSegmentationResult | VideoSegmentationResult
        if video:
            result = await parse_responses_stream(_sam_events(s.events), video_segmentation_format()).final_result()
        else:
            result = await parse_responses_stream(_sam_events(s.events), image_segmentation_format()).final_result()
        boxes: dict[tuple[str, int | None], SegmentationBoxRecord] = {}
        masks: dict[tuple[str, int | None], SegmentationMaskRecord] = {}
        for rec in result.records:
            if isinstance(rec, SegmentationBoxRecord):
                boxes[(rec.object_id, rec.frame.frame_index if rec.frame else None)] = rec
            elif isinstance(rec, SegmentationMaskRecord):
                masks[(rec.object_id, rec.frame.frame_index if rec.frame else None)] = rec
        objects = []
        for key in sorted(set(boxes) | set(masks), key=lambda k: (k[1] or 0, k[0])):
            b, m = boxes.get(key), masks.get(key)
            svg = None
            box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
            conf: float | None = None
            if m is not None:
                svg = _translate_path(decode_mask_to_svg_path(m.mask), m.bounds.left, m.bounds.top)
                box = (m.bounds.left, m.bounds.top, m.bounds.right, m.bounds.bottom)
                conf = m.confidence
            if b is not None:
                box = (b.left, b.top, b.right, b.bottom)
                conf = b.confidence if b.confidence is not None else conf
            objects.append(SamObject(object_id=key[0], box_px=box, conf=conf, mask_svg=svg, frame=key[1]))
        return objects, result.raw_output

    async def sam_image(
        self, frame: bytes, prompt: str | Box, *, lane: str, household: str | None = None,
        session_id: str | None = None, frame_hash: str | None = None, timeout_s: float | None = None,
        household_cap_usd: float | None = None, origin: str | None = None,
    ) -> SamResult:
        """Segment one concept (a short noun phrase) or one box (0-1000 grid) on one frame. Cached by frame
        hash plus prompt: the same view and prompt never calls SAM twice."""
        r = self.config.role("sam")
        im = self._prepare([frame])[0]
        key = (frame_hash or im.sha256, json.dumps(prompt), r.model)
        hit = self._sam_cache.get(key)
        if hit is not None:
            self._sam_cache.move_to_end(key)
            return SamResult(**{**hit.__dict__, "cached": True})
        body = self._sam_body(r, self._sam_prompt(prompt, im.width, im.height),
                              {"type": "input_image", "image_url": im.data_url()})
        params = {"model": r.model, "store": False, "stream": True, "n_images": 1, "image_px": self.config.max_px,
                  "mask_encoding": body["metadata"]["mask_encoding"]}
        s, _, usd, _ = await self._responses_call(
            kind="sam_image", role=r, body=body, params=params, lane=lane, household=household,
            session_id=session_id, household_cap_usd=household_cap_usd, estimate=pricing.SAM_IMAGE_USD,
            timeout_s=timeout_s or r.timeout_s, origin=origin,
        )
        objects, raw = await self._parse_sam(s, video=False)
        result = SamResult(im.width, im.height, objects, raw, s.latency_ms, usd, request_sha256(body))
        self._sam_cache[key] = result
        while len(self._sam_cache) > SAM_CACHE_SIZE:
            self._sam_cache.popitem(last=False)
        return result

    async def sam_video(
        self, clip: bytes, prompt: str | Box, *, width: int, height: int, frames: int, lane: str,
        household: str | None = None, session_id: str | None = None, household_cap_usd: float | None = None,
        timeout_s: float | None = None, origin: str | None = None,
    ) -> SamResult:
        """Track one concept or box through a short MP4 (H.264, faststart). width/height are the clip's
        displayed size; frames its frame count (for the budget check; billing has a 50-frame minimum)."""
        r = self.config.role("sam_video")
        media = {"type": "input_video", "video_url": "data:video/mp4;base64," + base64.b64encode(clip).decode()}
        body = self._sam_body(r, self._sam_prompt(prompt, width, height), media)
        params = {"model": r.model, "store": False, "stream": True, "n_images": 0,
                  "mask_encoding": body["metadata"]["mask_encoding"]}
        s, _, usd, _ = await self._responses_call(
            kind="sam_video", role=r, body=body, params=params, lane=lane, household=household,
            session_id=session_id, household_cap_usd=household_cap_usd, estimate=pricing.sam_video_usd(frames),
            timeout_s=timeout_s or r.timeout_s, origin=origin,
        )
        objects, raw = await self._parse_sam(s, video=True)
        return SamResult(width, height, objects, raw, s.latency_ms, usd, request_sha256(body))

    # ---- Voice ---------------------------------------------------------------------------------------------

    async def transcribe(
        self, audio: bytes, language_bias: Sequence[str] = ("Dutch", "English"), keywords: Sequence[str] = (),
        *, lane: str, household: str | None = None, session_id: str | None = None,
        household_cap_usd: float | None = None, timeout_s: float | None = None, origin: str | None = None,
    ) -> Transcript:
        """File transcription of a WAV (mono, 16-bit PCM, 16 or 24 kHz)."""
        r = self.config.role("transcribe")
        try:
            with wave.open(io.BytesIO(audio)) as w:
                ok = w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() in (16000, 24000)
                seconds = w.getnframes() / w.getframerate()
        except (wave.Error, EOFError):
            ok = False
        if not ok:
            raise ValueError("audio must be WAV, mono, 16-bit PCM, 16 or 24 kHz")
        request = {"mode": "PUSH_TO_TALK", "model": r.model, "audioEncoding": "WAV",
                   "languageBias": list(language_bias), "keywords": list(keywords)}
        logged = {"request": request, "audio_sha256": hashlib.sha256(audio).hexdigest()}
        sha = request_sha256(logged)
        estimate = pricing.transcribe_usd(seconds) + 0.0001
        self._check(lane, estimate, household, household_cap_usd)
        if self.mode == "record":
            save_request_body(self.private, sha, logged)
        base = {"role": r.name, "request_sha256": sha, "model": r.model, "lane": lane, "household": household,
                "session_id": session_id, "mode": self.ledger_mode, "provider": self.provider, "origin": origin,
                "probe": r.name,
                "audio_seconds": round(seconds, 2)}
        try:
            async with asyncio.timeout(timeout_s or r.timeout_s):
                latency, status, data = await self.backend.transcribe(request, audio, sha=sha)
        except TimeoutError:
            self._ledger_line(**base, status="timeout", usd=estimate, error="timeout (usd is the estimate)")
            err = CallTimeout(f"transcribe passed its {timeout_s or r.timeout_s:g} s timeout")
            err.usd = estimate
            raise err from None
        usd = pricing.transcribe_usd(seconds) if status == 200 else 0.0
        got = data.get("model")  # the transcription reply names no model today; asserted whenever it does
        self._ledger_line(**base, response_model=got, status="completed" if status == 200 else f"http_{status}",
                          latency_s=round(latency / 1000, 3), usd=round(usd, 6),
                          error=None if status == 200 else json.dumps(data)[:300])
        params = {"model": r.model, "store": False, "stream": False, "language_bias": list(language_bias)}
        self._record(lane, sha, "transcribe", r, params, [(latency, {"type": "asr.response", "body": data})], got,
                     latency, usd, http_status=status)
        if status != 200:
            raise CallFailed(f"transcribe: HTTP {status}: {json.dumps(data)[:300]}")
        if got is not None:
            self._assert_model(r, got)
        text = str(data.get("transcript") or data.get("text") or "")
        return Transcript(text=text, body=data, latency_ms=latency, usd=usd, request_sha256=sha)
