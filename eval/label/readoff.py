"""The hazard read-off (eval/hazard.py, end to end): filmed clips of the phone, one reading per spoken line.

A clip is a video in FIXIT_PRIVATE/clips-raw/hazard/ (screen and sound, 60 fps). A reading names the line, the
time the spoken line ends and the time of the first frame that shows STOP (null: no STOP in the clip), both in ms
from the start of the clip, with the phone and the network it was filmed on. The readings file holds one reading
per line, sorted by line id; reading a line again replaces it. The clip itself never enters the repo, only its hash.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from eval.hazard import FPS, READINGS, SCRIPT, lines
from eval.label.store import LabelError, _safe, read_jsonl

VIDEO = (".mp4", ".mov", ".webm", ".mkv")
FREE_TEXT_MAX = 60


def clip_dir(private: Path) -> Path:
    return private / "clips-raw" / "hazard"


def clips(private: Path) -> list[dict[str, Any]]:
    d = clip_dir(private)
    if not d.is_dir():
        return []
    return [{"clip_id": p.stem, "name": p.name, "bytes": p.stat().st_size}
            for p in sorted(d.iterdir()) if p.suffix.lower() in VIDEO and not p.name.startswith(".")]


def clip_path(private: Path, clip_id: str) -> Path:
    _safe(clip_id, "clip_id")
    for p in sorted(clip_dir(private).glob(f"{clip_id}.*")):
        if p.suffix.lower() in VIDEO:
            return p
    raise LabelError(f"no clip {clip_id} in clips-raw/hazard/")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):  # a 60 fps clip runs to gigabytes
            h.update(chunk)
    return h.hexdigest()


def _ms(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        raise LabelError(f"{what} must be a time in ms")
    return round(float(v), 1)


def _text(v: Any, what: str) -> str:
    s = str(v or "").strip()
    if not s or len(s) > FREE_TEXT_MAX:
        raise LabelError(f"{what} is needed, in at most {FREE_TEXT_MAX} characters (the model, not whose it is)")
    return s


def save_reading(private: Path, form: dict[str, Any], *, readings_path: Path = READINGS,
                 script_path: Path = SCRIPT) -> dict[str, Any]:
    known = {x.id for x in lines(script_path)}
    line_id = str(form.get("line_id", ""))
    if line_id not in known:
        raise LabelError(f"line {line_id!r} is not in eval/hazard.toml")
    path = clip_path(private, str(form.get("clip_id", "")))
    end = _ms(form.get("speech_end_ms"), "speech_end_ms")
    stop = None if form.get("stop_shown_ms") is None else _ms(form.get("stop_shown_ms"), "stop_shown_ms")
    if stop is not None and stop <= end:
        raise LabelError("STOP must show after the spoken line ends")
    fps = form.get("fps", FPS)
    if not isinstance(fps, int) or not 24 <= fps <= 240:
        raise LabelError("fps must be the clip's frame rate")
    reading = {"clip_id": path.stem, "sha256": sha256(path), "line_id": line_id, "speech_end_ms": end,
               "stop_shown_ms": stop, "fps": fps, "phone": _text(form.get("phone"), "phone"),
               "network": _text(form.get("network"), "network")}
    rest = [r for r in read_jsonl(readings_path) if r["line_id"] != line_id]
    out = sorted([*rest, reading], key=lambda r: r["line_id"])
    readings_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    return reading


def state(private: Path, readings_path: Path = READINGS, script_path: Path = SCRIPT) -> dict[str, Any]:
    return {"lines": [x.__dict__ for x in lines(script_path)], "clips": clips(private),
            "readings": read_jsonl(readings_path), "fps": FPS}
