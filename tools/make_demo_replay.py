"""Build demo/replays/demo/: a short replay that `make demo` plays with no key, no camera and no network.

    uv run python tools/make_demo_replay.py [--fetch]

It is made, not recorded: the steps and quotes come from the fictional Acme WM-0000 card
(contracts/examples/card/acme-wm-0000.json), the stop script from rules/nl.yaml, and the two frames are freely
licensed Wikimedia Commons photos (demo/replays/demo/CREDITS.md). --fetch downloads and crops them again; without
it the committed frames are reused. Every message is validated against contracts/ws.schema.json.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import contracts  # noqa: E402

OUT = ROOT / "demo/replays/demo"
CARD = ROOT / "contracts/examples/card/acme-wm-0000.json"
RULE = "nl.mains.casing"
W, H = 1024, 768

# (commons file, licence, crop box on the 1280 px thumbnail, sha256 of the frame as reviewed). Each crop was looked at
# by a person: no brand mark, no text, no face, no house identifier in frame. A frame that changes must be looked at
# again and its sha256 updated (tests/l0/test_demo.py holds the frames to these).
FRAMES = {
    "f_front": ("Domestic_washing_machine_Germany.jpg", "CC0", (200, 650, 1160, 1370),
                "5bde1e98481fa1fad0ecf09c1c6c66ea1c2f6cc753b9fa09a0a6c98d4451e93e"),
    "f_casing": ("Washing_machine-_without_front.jpg", "Public domain", (0, 0, 1280, 960),
                 "2758f53b393ba96258fd98f2b3d113ab16746e2e841f811e74345c7f73037fb6"),
}
THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/{h}/{file}/1280px-{file}"


def fetch(file: str, box: tuple[int, int, int, int]) -> bytes:
    import hashlib

    h = hashlib.md5(file.encode()).hexdigest()
    url = THUMB.format(h=f"{h[0]}/{h[:2]}", file=file)
    req = urllib.request.Request(url, headers={"User-Agent": "fix-it-lens-demo-builder/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        im = Image.open(io.BytesIO(r.read())).convert("RGB")
    im = im.crop(box).resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)  # a fresh JPEG: no EXIF carried over
    return buf.getvalue()


def rect(x1: int, y1: int, x2: int, y2: int) -> str:
    return f"M{x1} {y1} L{x2} {y1} L{x2} {y2} L{x1} {y2} Z"


def events() -> list[dict[str, Any]]:
    card = json.loads(CARD.read_text())
    job = card["jobs"][0]
    steps = job["steps"]
    rule = next(r for r in yaml.safe_load((ROOT / "rules/nl.yaml").read_text())["rules"] if r["id"] == RULE)
    out: list[dict[str, Any]] = []

    def s2c(t: float, msg: dict[str, Any], definition: str) -> None:
        msg = {**msg, "seq": len(out) + 1, "t_ms": t}
        contracts.validate("ws", msg, definition)
        out.append({"t_ms": t, "dir": "s2c", "kind": msg["type"], "msg": msg})

    def frame(t: float, frame_id: str) -> None:
        out.append({"t_ms": t, "dir": "c2s", "kind": "frame",
                    "msg": {"type": "frame", "seq": len(out) + 1, "t_ms": t, "frame_id": frame_id,
                            "jpeg_1024": None}})

    def step(t: float, i: int) -> None:
        st = steps[i]
        s2c(t, {"type": "step", "job_id": job["id"], "step_id": st["id"], "index": i, "of": len(steps),
                "text": st["text"]["en"], "page": st["quote"]["page"], "quote": st["quote"]["text"],
                "view": st["view"], "power": st["power"]}, "StepMsg")

    def check(t: float, i: int, clause: str) -> None:
        s2c(t, {"type": "checking", "step_id": steps[i]["id"], "check_id": f"c{i}"}, "Checking")
        s2c(t + 2400, {"type": "verdict", "verdict": {
            "result": "done", "clause_ids": [clause], "witnesses": {"judge": "done", "sam": "frame",
                                                                    "sam_result": "done"},
            "latency_ms": 2400}}, "VerdictMsg")

    frame(0, "f_front")
    s2c(400, {"type": "sentence", "id": "u1.s1", "text": f"Let's {job['title']['en'].lower()}."}, "Sentence")
    step(900, 0)
    s2c(1200, {"type": "sentence", "id": "u1.s2", "text": "First, pull the plug out of the socket."}, "Sentence")
    check(4200, 0, steps[0]["done_when"][0]["id"])
    step(7400, 1)
    s2c(7700, {"type": "outline", "part_id": "filter_flap", "kind": "target", "mask_svg": rect(690, 648, 850, 728),
               "frame_id": "f_front", "conf": 0.86, "label": "filter flap"}, "Outline")
    s2c(8000, {"type": "sentence", "id": "u2.s1", "text": "The flap is at the bottom right. I've outlined it."},
        "Sentence")
    check(11000, 1, steps[1]["done_when"][0]["id"])
    step(14200, 2)
    frame(17500, "f_casing")
    s2c(17800, {"type": "zone", "zone_id": "casing", "rule_id": RULE, "ring_cm": rule["ring_cm"],
                "mask_svg": rect(496, 196, 860, 330), "frame_id": "f_casing"}, "ZoneMsg")
    s2c(18100, {"type": "safety", "state": {"state": "STOP", "rule_id": RULE,
                                            "evidence": "the back panel is off and the mains board is exposed",
                                            "script": rule["script"]["en"]}}, "SafetyMsg")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download and crop the Commons frames again")
    args = ap.parse_args()
    (OUT / "frames").mkdir(parents=True, exist_ok=True)
    for fid, (file, _licence, box, _sha) in FRAMES.items():
        path = OUT / "frames" / f"{fid}.jpg"
        if args.fetch or not path.exists():
            path.write_bytes(fetch(file, box))
    meta = {"session_id": "demo", "household": None, "appliance_id": None, "card": None,
            "job_id": "clean_debris_filter", "started_at": "2026-10-27T09:00:00Z", "mode": "api", "lang": "en",
            "country": "nl", "contracts_version": contracts.VERSION}
    contracts.validate("session", meta, "SessionMeta")
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    (OUT / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events()))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
