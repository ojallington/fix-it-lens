"""The curate step: the only way a recording enters the public replays folder.

    FIXIT_CALLS=record uv run python deploy/curate.py SESSION_DIR --id NAME [--blur FRAME[:x,y,w,h]] ...
    uv run python deploy/curate.py SESSION_DIR --id NAME --approve DIGEST [--into demo/replays] [--golden]

The first form checks the recording and stages a public copy in FIXIT_PRIVATE/curate/NAME/:
- every distinct frame is looked at by the vision model (role judge, lane l5) for faces, house identifiers (house
  number, street sign, name, address, a window into a neighbour's), rating plates and serial numbers, and brand
  marks, and for any text it can read; what it boxes is blurred, anything it finds but cannot box blurs the whole
  frame, and so does visible text matching a line of FIXIT_PRIVATE/identifiers.txt;
- every event line loses the household (token and id), the plate photos and plate reads, and the audio; text
  matching identifiers.txt, a postcode, an e-mail, a phone number or a serial-like code is masked;
- review.html shows every staged frame with its flags, every masked line, and the digest of what is staged.
Look at every frame in review.html yourself. Anything the check missed: --blur FRAME (the whole frame) or
--blur FRAME:x,y,w,h (fractions of the frame), and run it again; the model is not asked twice about a frame.
A frame whose check failed blurs whole unless you name it in --blur.

The second form copies the staged recording into --into/NAME only when its digest is the one you approved, so an
approval covers exactly what was reviewed; --golden also writes --into/GOLDEN, so ?replay=golden opens it. Nothing
is ever copied from SESSION_DIR directly.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import io
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "l5"
PUBLIC_HOUSEHOLD = "demo"
DROP_KINDS = {"plate_photo", "plate_read"}  # a plate photo and what was read off it never go public
BOX = {"type": "object", "additionalProperties": False, "required": ["x", "y", "w", "h"],
       "properties": {k: {"type": "number"} for k in ("x", "y", "w", "h")}}
FOUND = {"type": "object", "additionalProperties": False, "required": ["what", "box"],
         "properties": {"what": {"type": "string"}, "box": {"anyOf": [BOX, {"type": "null"}]}}}
CHECK_FORMAT = {
    "type": "json_schema", "name": "PublicFrameCheck", "strict": True,
    "schema": {"type": "object", "additionalProperties": False,
               "required": ["faces", "identifiers", "plates", "brands", "text_seen"],
               "properties": {"faces": {"type": "array", "items": FOUND},
                              "identifiers": {"type": "array", "items": FOUND},
                              "plates": {"type": "array", "items": FOUND},
                              "brands": {"type": "array", "items": FOUND},
                              "text_seen": {"type": "array", "items": {"type": "string"}}}},
}
CHECK_PROMPT = (
    "This photo will be shown publicly. List everything in it that must be hidden, each with a box as fractions of "
    "the image (x, y = top-left; w, h = size; every value between 0 and 1, never pixels), or box null when you "
    "cannot place it:\n"
    "- faces: any human face or head, also partial, in a mirror, a photo or a screen reflection;\n"
    "- identifiers: anything that could locate or name a household: house number, street or place name, a name on "
    "a letter, parcel or label, an address, a view out of a window to a recognisable street;\n"
    "- plates: an appliance rating plate or sticker, a serial number, a barcode or QR code;\n"
    "- brands: a brand logo or brand name on anything.\n"
    "text_seen: every piece of text you can read, verbatim. Return empty lists when there is nothing."
)
MASKS = [
    ("postcode", re.compile(r"\b\d{4} ?[A-Z]{2}\b")),
    ("e-mail", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("phone", re.compile(r"(?<!\w)\+?\d[\d ()-]{8,}\d\b")),
    ("serial", re.compile(r"(?i)\b(?:s/?n|serial|fd|e-?nr|z-?nr)\b\s*[:.]?\s*[\w/-]*\d[\w/-]*")),
]


TEXT_KEYS = {"text", "quote", "args_short", "result_short", "error", "value", "reason", "what"}


class Responder(Protocol):
    async def respond(self, role: str, input: Any, **kw: Any) -> Any: ...


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @classmethod
    def parse(cls, d: dict[str, float] | None) -> Box | None:
        """A box in fractions of the frame, or None when there is none or it cannot be trusted: a value outside
        0..1 (a model that answered in pixels or per mille) or no area. None blurs the whole frame."""
        if not d or any(not 0.0 <= d[k] <= 1.0 for k in ("x", "y", "w", "h")):
            return None
        x, y = d["x"], d["y"]
        w, h = min(d["w"], 1.0 - x), min(d["h"], 1.0 - y)
        return cls(x, y, w, h) if w > 0.005 and h > 0.005 else None


@dataclass
class FrameReview:
    frame_id: str
    flags: list[str] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)
    whole: bool = False
    failed: str | None = None


@dataclass
class Staged:
    dir: Path
    digest: str
    frames: list[FrameReview]
    masked: list[str]


def identifiers(private: Path) -> list[str]:
    f = private / "identifiers.txt"
    if not f.is_file():
        return []
    return [ln.strip() for ln in f.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def _mask(text: str, ids: list[str], why: list[str]) -> str:
    for n, ident in enumerate(ids, 1):
        pat = re.compile(re.escape(ident), re.I)
        if pat.search(text):
            why.append(f"identifiers.txt line {n}")
            text = pat.sub("▇▇▇", text)
    for name, pat in MASKS:
        if pat.search(text):
            why.append(name)
            text = pat.sub("▇▇▇", text)
    return text


def _scrub(value: Any, ids: list[str], why: list[str], key: str = "") -> Any:
    """Masks the words people and models said (TEXT_KEYS); ids, hashes and timings are left as they are."""
    if isinstance(value, str):
        return _mask(value, ids, why) if key in TEXT_KEYS else value
    if isinstance(value, list):
        return [_scrub(v, ids, why, key) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v, ids, why, k) for k, v in value.items()}
    return value


def public_events(lines: list[dict[str, Any]], ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """The event lines with nothing private left in them, and what was masked (by line, never the text)."""
    out, masked = [], []
    for n, e in enumerate(lines, 1):
        if e.get("kind") in DROP_KINDS:
            masked.append(f"line {n} ({e['kind']}): dropped")
            continue
        e = json.loads(json.dumps(e))
        msg = e.get("msg") if isinstance(e.get("msg"), dict) else {}
        if "household" in msg:
            msg["household"] = PUBLIC_HOUSEHOLD  # the hello carries the household's bearer token
        if e.get("kind") == "utterance":
            msg["audio_wav_b64"] = None
        why: list[str] = []
        e = _scrub(e, ids, why)
        if why:
            masked.append(f"line {n} ({e.get('kind')}): {', '.join(sorted(set(why)))} masked")
        out.append(e)
    return out, masked


def blur(jpeg: bytes, boxes: list[Box], whole: bool) -> bytes:
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    W, H = im.size
    regions = [(0, 0, W, H)] if whole else [
        (int(b.x * W), int(b.y * H), max(int((b.x + b.w) * W), int(b.x * W) + 1),
         max(int((b.y + b.h) * H), int(b.y * H) + 1)) for b in boxes]
    for r in regions:
        px, py = max(8, (r[2] - r[0]) // 4), max(8, (r[3] - r[1]) // 4)  # well past the box: a model's box is tight
        r = (max(r[0] - px, 0), max(r[1] - py, 0), min(r[2] + px, W), min(r[3] + py, H))
        part = im.crop(r)
        small = part.resize((max(1, part.width // 24), max(1, part.height // 24)))
        im.paste(small.resize(part.size, Image.Resampling.NEAREST).filter(ImageFilter.GaussianBlur(6)), r[:2])
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def parse_blur(specs: list[str]) -> dict[str, list[Box] | None]:
    """--blur FRAME (whole) or FRAME:x,y,w,h; None means the whole frame."""
    out: dict[str, list[Box] | None] = {}
    for s in specs:
        fid, _, box = s.partition(":")
        if not box:
            out[fid] = None
            continue
        x, y, w, h = (float(v) for v in box.split(","))
        prev = out.setdefault(fid, [])
        if prev is not None:
            prev.append(Box(x, y, w, h))
    return out


async def check_frame(client: Responder, jpeg: bytes, ids: list[str]) -> dict[str, Any]:
    r = await client.respond("judge", CHECK_PROMPT, images=[jpeg], format=CHECK_FORMAT, lane=LANE)
    found: dict[str, Any] = r.parsed()
    seen = " ".join(found.get("text_seen") or [])
    found["identifier_lines"] = [n for n, ident in enumerate(ids, 1) if ident.lower() in seen.lower()]
    return found


def review_frame(fid: str, found: dict[str, Any] | None, error: str | None) -> FrameReview:
    rv = FrameReview(fid)
    if found is None:
        rv.failed, rv.whole = error or "not checked", True
        rv.flags.append(f"check failed ({rv.failed}): blurred whole")
        return rv
    for kind in ("faces", "identifiers", "plates", "brands"):
        for item in found.get(kind) or []:
            box = Box.parse(item.get("box"))
            if box is None:
                rv.whole = True
            if box is not None:
                rv.boxes.append(box)
            rv.flags.append(f"{kind[:-1]}: {item.get('what', '')}" + ("" if box else " (no usable box: whole frame)"))
    if found.get("identifier_lines"):
        rv.whole = True
        rv.flags.append("text matching identifiers.txt line " + ", ".join(map(str, found["identifier_lines"])))
    return rv


def digest_of(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.name not in ("review.html", "checks.json", "report.json"):
            h.update(str(p.relative_to(d)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


async def stage(session: Path, name: str, private: Path, client: Responder,
                blur_specs: list[str] = ()) -> Staged:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name):
        raise SystemExit(f"--id {name!r}: lower-case letters, digits and '-' only")
    meta = json.loads((session / "meta.json").read_text())
    lines = [json.loads(x) for x in (session / "events.jsonl").read_text().splitlines() if x.strip()]
    ids = identifiers(private)
    out = private / "curate" / name
    cache_f = out / "checks.json"
    cache: dict[str, Any] = json.loads(cache_f.read_text()) if cache_f.is_file() else {}
    if out.exists():
        shutil.rmtree(out)
    (out / "frames").mkdir(parents=True)

    events, masked = public_events(lines, ids)
    manual = parse_blur(list(blur_specs))
    frames: list[FrameReview] = []
    for jpg in sorted((session / "frames").glob("*.jpg")):
        fid, data = jpg.stem, jpg.read_bytes()
        key = hashlib.sha256(data + CHECK_PROMPT.encode()).hexdigest()  # a new question asks again
        if key not in cache or "error" in cache[key]:
            try:
                cache[key] = {"found": await check_frame(client, data, ids)}
            except Exception as e:  # noqa: BLE001  # any failure: the frame blurs whole, and the review says why
                cache[key] = {"error": type(e).__name__}
        rv = review_frame(fid, cache[key].get("found"), cache[key].get("error"))
        if fid in manual:
            boxes = manual[fid]
            if boxes is None:
                rv.whole = True
                rv.flags.append("blurred whole (--blur)")
            else:
                rv.boxes += boxes
                rv.flags.append(f"{len(boxes)} box(es) blurred (--blur)")
                if not (cache[key].get("found") or {}).get("identifier_lines"):
                    rv.whole = False  # reviewed by hand: the named boxes are what needs hiding
        (out / "frames" / jpg.name).write_bytes(blur(data, rv.boxes, rv.whole) if (rv.boxes or rv.whole)
                                                else _reencode(data))
        frames.append(rv)

    public_meta = {k: v for k, v in meta.items() if k not in ("household", "appliance_id")}
    public_meta["session_id"] = name
    from contracts.models.session import SessionMeta

    SessionMeta.model_validate(public_meta)  # as the replay host loads it: a copy that cannot play stops here
    (out / "meta.json").write_text(json.dumps(public_meta, indent=2) + "\n")
    with (out / "events.jsonl").open("w") as f:
        for e in events:
            if isinstance(e.get("msg"), dict) and "session_id" in e["msg"]:
                e["msg"]["session_id"] = name
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    cache_f.write_text(json.dumps(cache))
    digest = digest_of(out)
    (out / "report.json").write_text(json.dumps({
        "digest": digest, "masked": masked,
        "frames": [{"frame": r.frame_id, "flags": r.flags, "whole": r.whole, "failed": r.failed} for r in frames],
    }, indent=2))
    (out / "review.html").write_text(review_html(name, digest, frames, masked))
    return Staged(out, digest, frames, masked)


def _reencode(jpeg: bytes) -> bytes:
    """Re-encoded through Pillow: no EXIF, no maker notes, no thumbnail left in the file."""
    return blur(jpeg, [], False)


def review_html(name: str, digest: str, frames: list[FrameReview], masked: list[str]) -> str:
    cards = "\n".join(
        f'<figure class="{"flag" if r.flags else ""}"><img src="frames/{html.escape(r.frame_id)}.jpg" loading="lazy">'
        f"<figcaption><b>{html.escape(r.frame_id)}</b> "
        + ("; ".join(html.escape(f) for f in r.flags) or "nothing found") + "</figcaption></figure>"
        for r in frames)
    lines = "".join(f"<li>{html.escape(m)}</li>" for m in masked) or "<li>nothing masked</li>"
    flagged = sum(1 for r in frames if r.flags)
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Review: {html.escape(name)}</title>
<style>body{{font:15px system-ui;margin:16px;background:#111;color:#eee}}main{{display:grid;
grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}}figure{{margin:0;background:#1c1c1b;
border-radius:10px;overflow:hidden}}figure.flag{{outline:2px solid #ff6b5e}}img{{width:100%;display:block}}
figcaption{{padding:8px;font-size:13px}}code{{font-size:16px}}</style>
<h1>Review: {html.escape(name)}</h1>
<p>{len(frames)} frames, {flagged} flagged and blurred. Look at every frame, not only the flagged ones: no face, no
plate or serial number, no house identifier, no brand mark. Then approve exactly this copy:</p>
<p><code>uv run python deploy/curate.py &lt;session&gt; --id {html.escape(name)} --approve {digest}</code></p>
<h2>Event lines</h2><ul>{lines}</ul><h2>Frames</h2><main>{cards}</main>
"""


def approve(name: str, digest: str, private: Path, into: Path, *, make_golden: bool = False) -> Path:
    staged = private / "curate" / name
    if not (staged / "report.json").is_file():
        raise SystemExit(f"{staged}: nothing staged; run the check first")
    now = digest_of(staged)
    if now != digest:
        raise SystemExit(f"the staged copy's digest is {now}, not {digest}: review it again (review.html)")
    dest = into / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for p in ("meta.json", "events.jsonl"):
        shutil.copy2(staged / p, dest / p)
    shutil.copytree(staged / "frames", dest / "frames")
    if make_golden:
        (into / "GOLDEN").write_text(name + "\n")  # ?replay=golden and / now open it; the links stay the same
    return dest


def _client(private: Path) -> Responder:
    import calls

    mode = os.environ.get("FIXIT_CALLS", "replay")
    fx = private / "fixtures"  # a check's reply describes a private frame: it is recorded here, never in the repo
    # a sealed copy (fixtures-sealed-1027) outlives the 7-day sweep of fixtures/: a re-curate replays its checks free
    sealed = sorted(p for p in private.glob("fixtures-sealed-*") if p.is_dir())
    return calls.make_client(mode, private=private, fixtures_root=fx,  # type: ignore[arg-type]
                             fixture_roots=[fx, *sealed])


def main() -> None:
    import calls

    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=Path, help="a recorded session folder (meta.json, events.jsonl, frames/)")
    ap.add_argument("--id", required=True, help="the public recording's name, e.g. demo")
    ap.add_argument("--blur", action="append", default=[], metavar="FRAME[:x,y,w,h]")
    ap.add_argument("--approve", metavar="DIGEST", help="copy the staged copy with this digest into --into")
    ap.add_argument("--into", type=Path, default=ROOT / "demo/replays")
    ap.add_argument("--golden", action="store_true", help="with --approve: make it the recording `golden` names")
    args = ap.parse_args()
    private = calls.private_dir()
    if private is None:
        raise SystemExit("FIXIT_PRIVATE is not set and the default private folder does not exist")
    if args.approve:
        dest = approve(args.id, args.approve, private, args.into, make_golden=args.golden)
        print(f"approved: {dest}" + (" (golden)" if args.golden else ""))
        return
    s = asyncio.run(stage(args.session, args.id, private, _client(private), args.blur))
    flagged = [r for r in s.frames if r.flags]
    print(f"staged {len(s.frames)} frames ({len(flagged)} flagged, blurred) and {len(s.masked)} event changes")
    for r in flagged:
        print(f"  {r.frame_id}: {'; '.join(r.flags)}")
    print(f"review: {s.dir / 'review.html'}\ndigest: {s.digest}")


if __name__ == "__main__":
    main()
