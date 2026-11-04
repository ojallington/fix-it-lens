"""Day-0 measurements : about $1 of live calls, recorded into fixtures/l0/.

    FIXIT_PRIVATE=... uv run python tools/day0.py [--only tokens,sam,judge,guide,voice,wonder]

Inputs come from $FIXIT_PRIVATE/day0/ (never the repo): dw_heater.jpg (a Wikimedia Commons dishwasher sump),
nl-3s.wav (3 s of the public Piper nl_NL sample), acme-wm-0000.pdf (tools/make_acme_manual.py). Every call goes
through calls/ in record mode: ledger-checked against lane l0, responses only in the fixtures, request bodies
only in the private folder. The measurements print as JSON lines.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
from dataclasses import replace
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import calls
import contracts
from calls.errors import CallError

LANE = "l0"
STEPS = ("tokens", "sam", "judge", "guide", "voice", "wonder")
LONG = 120.0  # day 0 measures latency; it does not enforce the per-role timeouts


def out(name: str, **row: Any) -> None:
    print(json.dumps({"measure": name, **row}, ensure_ascii=False), flush=True)


def jpeg(im: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def inline(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """Resolve local #/$defs refs, so a text.format schema stands alone."""
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return inline(defs[ref.removeprefix("#/$defs/")], defs)
        return {k: inline(v, defs) for k, v in schema.items() if k not in ("$schema", "$id", "$defs")}
    if isinstance(schema, list):
        return [inline(v, defs) for v in schema]
    return schema


def planted(still: bytes, box: tuple[float, float, float, float] | None, digit: str) -> bytes:
    """The overlay segment_part would return: an outline, plus a digit only a reader of the image can know."""
    im = Image.open(io.BytesIO(still)).convert("RGB")
    im.thumbnail((1024, 1024))
    d = ImageDraw.Draw(im)
    w, h = im.size
    x1, y1, x2, y2 = box or (w * 0.3, h * 0.3, w * 0.7, h * 0.7)
    d.rectangle((x1, y1, x2, y2), outline=(0, 220, 255), width=6)
    font = ImageFont.load_default(size=int(h * 0.18))
    d.text((x2 + 10 if x2 + 10 < w * 0.85 else max(x1 - h * 0.15, 0), y1), digit, fill=(255, 40, 40), font=font,
           stroke_width=4, stroke_fill=(255, 255, 255))
    return jpeg(im)


async def tokens(c: calls.Client, still: bytes) -> None:
    for px in (640, 768, 1024, 1280):
        c.config = replace(c.config, max_px=px)
        try:
            n = await c.count_tokens("guide", "Describe this.", images=[still], lane=LANE)
            text_only = await c.count_tokens("guide", "Describe this.", lane=LANE)
            out("input_tokens", px=px, tokens=n, image_tokens=n - text_only)
        except CallError as e:
            out("input_tokens", px=px, error=str(e))
    c.config = replace(c.config, max_px=1024)


async def sam(c: calls.Client, still: bytes) -> tuple[float, float, float, float] | None:
    best = None
    for concept in ("coarse filter", "mesh filter"):
        try:
            r = await c.sam_image(still, concept, lane=LANE, timeout_s=LONG)
        except CallError as e:
            out("sam", concept=concept, error=str(e))
            continue
        out("sam", concept=concept, px=[r.width, r.height], latency_ms=round(r.latency_ms), usd=r.usd,
            objects=[{"conf": o.conf, "box_1000": r.box_1000(o), "svg_chars": len(o.mask_svg or "")}
                     for o in r.objects])
        if r.objects and best is None:
            o = max(r.objects, key=lambda o: o.conf or 0)
            best = o.box_px
    return best


async def judge(c: calls.Client, still: bytes, box: tuple[float, float, float, float] | None) -> None:
    im = Image.open(io.BytesIO(still)).convert("RGB")
    im.thumbnail((1024, 1024))
    removed = im.copy()
    x1, y1, x2, y2 = box or (im.width * 0.3, im.height * 0.4, im.width * 0.7, im.height * 0.8)
    ImageDraw.Draw(removed).rectangle((x1, y1, x2, y2), fill=(70, 70, 72))  # the filter "lifted out"
    j = contracts.schema("judge")
    fmt = {"type": "json_schema", "name": "StepCheck", "strict": True,
           "schema": inline(j["$defs"]["JudgeOutput"], j["$defs"])}
    rules = ("You check ONE repair step from camera images. Judge only what is visible. For each clause: first say "
             "whether the thing it is about is visible; if not, state = unclear. Then give the evidence and point to "
             "it on NOW (0-1000 grid). When unsure, choose unclear.")
    clauses = [{"id": "c1", "text": "The mesh filter is no longer in the sump"},
               {"id": "c2", "text": "The sump opening is visible and empty"}]
    content = [{"type": "input_text", "text": f"Step: lift the filter out.\nClauses (answer in this order): "
                                              f"{json.dumps(clauses)}\nBEFORE (when the step was given):"},
               {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg(im)).decode()},
               {"type": "input_text", "text": "NOW:"},
               {"type": "input_image",
                "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg(removed)).decode()}]
    try:
        r = await c.respond("judge", [{"type": "message", "role": "user", "content": content}], format=fmt,
                            instructions=rules, lane=LANE, timeout_s=LONG)
        parsed = r.parsed()
        out("judge", latency_ms=round(r.latency_ms), ttft_ms=r.ttft_ms and round(r.ttft_ms), usd=round(r.usd, 6),
            usage=r.usage.__dict__, order_kept=[x.get("clause_id") for x in parsed.get("clauses", [])] == ["c1", "c2"],
            states=[x.get("state") for x in parsed.get("clauses", [])],
            reasoning_summary=bool(r.reasoning_summary), valid=_valid(parsed))
    except CallError as e:
        out("judge", error=str(e))


def _valid(parsed: Any) -> bool:
    try:
        contracts.validate("judge", parsed, "JudgeOutput")
        return True
    except Exception:
        return False


async def guide(c: calls.Client, still: bytes, box: tuple[float, float, float, float] | None) -> None:
    tools = [{"type": "function", "name": "segment_part", "description": "Outline one card part in the frame.",
              "parameters": {"type": "object", "additionalProperties": False, "required": ["part_id"],
                             "properties": {"part_id": {"type": "string"}}}, "strict": True}]
    overlay = "data:image/jpeg;base64," + base64.b64encode(planted(still, box, "7")).decode()
    ask = "Show me the filter. Then tell me the number written in red on the outline image, or say 'none'."
    history: list[dict[str, Any]] = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": ask}]},
        {"type": "function_call", "call_id": "call_day0", "name": "segment_part",
         "arguments": json.dumps({"part_id": "mesh_filter"})},
    ]
    variants = {
        "in_tool_output": [{"type": "function_call_output", "call_id": "call_day0",
                            "output": [{"type": "input_text", "text": "Outlined mesh_filter."},
                                       {"type": "input_image", "image_url": overlay}]}],
        "user_fallback": [{"type": "function_call_output", "call_id": "call_day0", "output": "Outlined mesh_filter."},
                          {"type": "message", "role": "user", "content": [
                              {"type": "input_text", "text": "The outline image:"},
                              {"type": "input_image", "image_url": overlay}]}],
    }
    for name, tail in variants.items():
        try:
            r = await c.respond("guide", history + tail, tools=tools, lane=LANE, timeout_s=LONG,
                                instructions="You guide a home repair. Answer in one short sentence.")
        except CallError as e:
            out("guide", variant=name, error=str(e))
            continue
        out("guide", variant=name, latency_ms=round(r.latency_ms), ttft_ms=r.ttft_ms and round(r.ttft_ms),
            usd=round(r.usd, 6), usage=r.usage.__dict__, read_digit="7" in r.text, text=r.text[:200],
            reasoning_summary=r.reasoning_summary[:200], tool_calls=[f.name for f in r.function_calls])
        if "7" in r.text:
            break  # the image in the tool output is seen; the fallback is not needed


async def voice(c: calls.Client, wav: bytes) -> None:
    try:
        t = await c.transcribe(wav, ("Dutch", "English"), lane=LANE)
        out("transcribe", latency_ms=round(t.latency_ms), usd=t.usd, text=t.text, keys=sorted(t.body))
    except (CallError, ValueError) as e:
        out("transcribe", error=str(e))


async def wonder(c: calls.Client, pdf: bytes) -> None:
    card = contracts.schema("card")
    item = {"type": "input_file", "filename": "acme-wm-0000.pdf",
            "file_data": "data:application/pdf;base64," + base64.b64encode(pdf).decode()}
    ask = ("Read this washer manual and return its repair card as JSON matching the schema: parts, red zones, the "
           "upkeep job with its interval and steps, each step with its page and a verbatim quote of at most two "
           "sentences in the manual's language.")
    msg = [{"type": "message", "role": "user", "content": [item, {"type": "input_text", "text": ask}]}]
    for strict in (False, None):
        fmt = None if strict is None else {"type": "json_schema", "name": "Card", "strict": strict,
                                           "schema": inline(card, card["$defs"])}
        try:
            r = await c.respond("appliance_file", msg, format=fmt, effort="medium", max_output_tokens=12000,
                                lane=LANE, timeout_s=600)
        except CallError as e:
            out("wonder", format=bool(fmt), error=str(e))
            continue
        try:
            parsed = r.parsed()
            contracts.validate("card", parsed)
            valid: Any = True
        except Exception as e:  # what fails is the finding
            valid = str(e)[:200]
        out("wonder", format=bool(fmt), reachable=True, latency_ms=round(r.latency_ms), usd=round(r.usd, 6),
            usage=r.usage.__dict__, card_valid=valid, chars=len(r.text))
        return
    out("wonder", reachable=False)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(STEPS))
    ap.add_argument("--dry", action="store_true", help="replay mode: every path up to the network, $0")
    args = ap.parse_args()
    only = set(args.only.split(","))
    priv = calls.private_dir()
    if priv is None:
        print("no private folder (FIXIT_PRIVATE)", file=sys.stderr)
        return 2
    d = priv / "day0"
    still = (d / "dw_heater.jpg").read_bytes()
    c = calls.make_client("replay" if args.dry else "record", private=priv)
    box = None
    if "tokens" in only:
        await tokens(c, still)
    if only & {"sam", "judge", "guide"}:
        box = await sam(c, still) if "sam" in only else None
    if "judge" in only:
        await judge(c, still, box)
    if "guide" in only:
        await guide(c, still, box)
    if "voice" in only:
        await voice(c, (d / "nl-3s.wav").read_bytes())
    if "wonder" in only:
        await wonder(c, (d / "acme-wm-0000.pdf").read_bytes())
    t = c.ledger.totals() if c.ledger else None
    out("ledger", lane_l0_usd=t and round(t.by_lane.get(LANE, 0.0), 6), total_usd=t and round(t.total, 6))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
