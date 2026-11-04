"""The server's eval rows (eval/rows.toml): the production code paths on labelled media, one JSON claim per line.

    python -m app.eval voice      # l1.voice.wer.keywords, l1.voice.wer.plain
    python -m app.eval plugout    # l1.plugout.unplugged, l1.plugout.false_unplugged
    python -m app.eval gastap     # l1.gastap.located
    python -m app.eval latency    # l1.latency.judge4_p95, l1.latency.sam_video_p95

Claim: {"id", "value", "unit", "n", "ci" (Wilson 95% in %, or null), "note"}. n=0 is unmeasured, not 0 of 0.

Replay by default (FIXIT_CALLS=replay, no key): the recorded responses are in fixtures/l1/<row>/. The live run is the
same command with FIXIT_CALLS=record, through calls/ and the ledger, and writes those fixtures. Labels are in
eval/labels/ (ids, hashes and truth only); the media stay in FIXIT_PRIVATE/clips-raw/<row>/: plate photos, voices and
other people's boilers never enter the repo. A label whose media is missing, or whose bytes do not match its sha256,
is skipped and counted in the note.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import calls
from app import config, locate, power, witness
from app.cards import read as read_card
from app.models import Models
from app.ports import NoHomeAssistant
from app.recorder import Recorder
from app.stepcheck import check_clip, stored
from contracts import VERSION
from contracts.models.card import Part
from contracts.models.session import SessionMeta

LABELS = config.ROOT / "eval" / "labels"
FIXTURES = config.ROOT / "fixtures" / "l1"
LANE = "l1"
WAITING = "not measured in this build"


# ---- small sums -----------------------------------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """The Wilson 95% interval for k of n, in percent."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * max(0.0, mid - half), 1), round(100 * min(1.0, mid + half), 1)]


def p95(xs: list[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def word_errors(ref: list[str], hyp: list[str]) -> int:
    """Substitutions + deletions + insertions (Levenshtein on words)."""
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


def claim(id: str, value: float | None, unit: str, n: int, ci: list[float] | None = None,
          note: str = "") -> dict[str, Any]:
    return {"id": id, "value": None if n == 0 else value, "unit": unit, "n": n, "ci": ci, "note": note}


def share(id: str, k: int, n: int, note: str) -> dict[str, Any]:
    return claim(id, round(100 * k / n, 1) if n else None, "%", n, wilson(k, n), f"{k} of {n}; {note}")


# ---- labels and media -----------------------------------------------------------------------------------------------


@dataclass
class Media:
    skipped: int = 0

    def items(self, row: str, ext: str) -> Iterator[tuple[dict[str, Any], bytes]]:
        """Each label of eval/labels/<row>.jsonl with its media bytes from FIXIT_PRIVATE/clips-raw/<row>/<id><ext>."""
        f = LABELS / f"{row}.jsonl"
        priv = config.private_dir()
        for line in f.read_text().splitlines() if f.exists() else []:
            if not line.strip():
                continue
            label = json.loads(line)
            p = priv / "clips-raw" / row / f"{label['id']}{ext}" if priv is not None else None
            data = p.read_bytes() if p is not None and p.exists() else None
            if data is None or hashlib.sha256(data).hexdigest() != label["sha256"]:
                self.skipped += 1
                continue
            yield label, data

    def note(self) -> str:
        return f"{self.skipped} labelled item(s) skipped: media missing or hash differs" if self.skipped else ""


def client_for(row: str) -> calls.Client:
    mode = os.environ.get("FIXIT_CALLS", "replay")
    if mode not in ("replay", "record"):
        raise SystemExit(f"FIXIT_CALLS={mode!r}: the eval runs replay, or record for the live run")
    return calls.make_client(mode, fixtures_root=FIXTURES / row)  # type: ignore[arg-type]


def models_for(client: calls.Client, row: str) -> Models:
    meta = SessionMeta(session_id=f"eval-l1-{row}", started_at=datetime.now(UTC), mode="api", lang="en",
                       contracts_version=VERSION)
    t0 = asyncio.get_event_loop().time()
    return Models(client, Recorder(None, meta), lambda: (asyncio.get_event_loop().time() - t0) * 1000,
                  household=None, session_id=meta.session_id, lane=LANE)


# ---- the rows -------------------------------------------------------------------------------------------------------


async def voice() -> list[dict[str, Any]]:
    """Word error rate of the production transcription, with the card's keywords (as a session sends them) and
    without. Label: {"id", "sha256", "lang", "reference", "card": "bosch/SMV4HAX40N"}; media <id>.wav."""
    from app.voice import card_keywords

    m, client = Media(), client_for("voice")
    models = models_for(client, "voice")
    err = {"keywords": 0, "plain": 0}
    n_words, clips, langs = 0, 0, set()
    for label, wav in m.items("voice", ".wav"):
        card = read_card(config.ROOT / "cards" / f"{label['card']}.yaml") if label.get("card") else None
        ref = words(label["reference"])
        for arm, kw in (("keywords", card_keywords(card) if card else []), ("plain", [])):
            t, _ = await models.transcribe(wav, kw)
            err[arm] += word_errors(ref, words(t.text))
        n_words += len(ref)
        clips += 1
        langs.add(label["lang"])
    note = f"{clips} clips, langs {','.join(sorted(langs)) or '-'}" + (f"; {m.note()}" if m.note() else "")
    return [claim(f"l1.voice.wer.{arm}", round(100 * e / n_words, 1) if n_words else None, "%", n_words, None, note)
            for arm, e in err.items()]


async def plugout() -> list[dict[str, Any]]:
    """The camera's plug-out call (SAM on plug and socket, decided in code; no HA): of the frames with the plug out,
    how many it calls out (target: 40 of 40); of the frames with the plug in, how many it wrongly calls out
    (target: 0 of 40). Label: {"id", "sha256", "truth": "in" | "out"}; media <id>.jpg."""
    m, client = Media(), client_for("plugout")
    models = models_for(client, "plugout")
    hit = {"out": [0, 0], "in": [0, 0]}  # truth -> [called out, n]
    for label, jpeg in m.items("plug", ".jpg"):
        ev = await power.power_evidence(models, stored(label["id"], 0.0, jpeg), NoHomeAssistant(), None)
        hit[label["truth"]][0] += ev.camera == "done"
        hit[label["truth"]][1] += 1
    note = m.note()
    return [share("l1.plugout.unplugged", *hit["out"], "plug-out frames called out" + (f"; {note}" if note else "")),
            share("l1.plugout.false_unplugged", *hit["in"],
                  "plug-in frames called out (wrongly)" + (f"; {note}" if note else ""))]


GAS_TAP = Part(id="gas_tap", names={"en": "gas tap", "nl": "gaskraan"}, locate="function",  # type: ignore[arg-type]
               cue="the gas isolation tap on the gas pipe into the boiler, usually a lever handle, often yellow",
               sam_text="gas valve", kind="red_zone")


def iou(a: tuple[int, int, int, int], b: list[int]) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


async def gastap() -> list[dict[str, Any]]:
    """The gas-tap confirm on other boilers: the production locate (Spark, by function) on a photo of a boiler's pipes
    finds the gas tap when its box overlaps the labelled box with IoU >= 0.5. Label: {"id", "source_url" | "consent",
    "sha256", "box": [x0, y0, x1, y1] on the 0-1000 grid}; media <id>.jpg."""
    m, client = Media(), client_for("gastap")
    models = models_for(client, "gastap")
    k = n = 0
    for label, jpeg in m.items("gastap", ".jpg"):
        found = await locate.locate(models, GAS_TAP, stored(label["id"], 0.0, jpeg), "en")
        k += found.box is not None and iou(found.box, label["box"]) >= 0.5
        n += 1
    return [share("l1.gastap.located", k, n, "boilers with the tap box IoU >= 0.5" +
                  (f"; {m.note()}" if m.note() else ""))]


class Timed:
    """The client, noting each call's latency as calls/ measured it (recorded with the fixture, so replay reports the
    live run's numbers)."""

    def __init__(self, inner: calls.Client) -> None:
        self.inner = inner
        self.ms: dict[str, list[float]] = {}

    def __getattr__(self, name: str) -> Callable[..., Any]:
        fn = getattr(self.inner, name)

        async def call(*a: Any, **kw: Any) -> Any:
            out = await fn(*a, **kw)
            role = a[0] if name == "respond" and a else name
            if getattr(out, "latency_ms", None) is not None:
                self.ms.setdefault(str(role), []).append(float(out.latency_ms))
            return out

        return call


async def latency() -> list[dict[str, Any]]:
    """RISKS rows 30/31: the 4-image judge (BEFORE, NOW_1, NOW_2, CROP) at minimal, and SAM video on a 2 s clip.
    Label: {"id", "sha256", "card", "step_id"}; media <id>.jsonl in clips-raw/latency/, one frame per line
    {"t_s", "jpeg_b64"}, the first being BEFORE and the rest 2 s of NOW."""
    import base64

    m = Media()
    timed = Timed(client_for("latency"))
    models = models_for(timed, "latency")  # type: ignore[arg-type]
    for label, raw in m.items("latency", ".jsonl"):
        frames = [json.loads(x) for x in raw.decode().splitlines() if x.strip()]
        jpegs = [(float(f["t_s"]), base64.b64decode(f["jpeg_b64"])) for f in frames]
        card = read_card(config.ROOT / "cards" / f"{label['card']}.yaml")
        await check_clip(card, label["step_id"], jpegs[0][1], jpegs[1:], witness="none", lane=LANE,
                         client=timed)  # type: ignore[arg-type]
        now = [stored(f"n{i}", t * 1000, j) for i, (t, j) in enumerate(jpegs[1:])]
        clip = witness.build_clip(now)
        step = next(s for j in card.jobs for s in j.steps if s.id == label["step_id"])
        part = next((p for p in card.parts if step.parts and p.id == step.parts[0].root), None)
        if clip is not None and part is not None:
            data, w, h = clip
            await models.sam_video(data, part.sam_text, width=w, height=h, frames=len(now),
                                   timeout_s=config.CALL_TIMEOUT_S)
    judge, video = timed.ms.get("judge", []), timed.ms.get("sam_video", [])
    note = m.note()
    return [claim("l1.latency.judge4_p95", round(p95(judge)) if judge else None, "ms", len(judge), None,
                  "4-image judge at minimal" + (f"; {note}" if note else "")),
            claim("l1.latency.sam_video_p95", round(p95(video)) if video else None, "ms", len(video), None,
                  "SAM 3.1 video on a 2 s clip" + (f"; {note}" if note else ""))]


ROWS = {"voice": voice, "plugout": plugout, "gastap": gastap, "latency": latency}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ROWS:
        print(f"usage: python -m app.eval {{{'|'.join(ROWS)}}}", file=sys.stderr)
        return 2
    for c in asyncio.run(ROWS[args[0]]()):
        print(json.dumps(c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
