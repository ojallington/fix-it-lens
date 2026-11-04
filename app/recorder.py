"""Record a session: FIXIT_PRIVATE/sessions/<id>/ with meta.json, events.jsonl, frames/<frame_id>.jpg and
audio/<utterance_id>.wav (session.schema.json). Frame bytes and audio are files, never event lines.

Lines are ClientEvent (`c2s`/`s2c`, the ws message minus its bytes) or StateEvent (`state`: the session's own moves,
the frame picks and every model call's outcome, which is what lets a replay re-run the session without the API).
A hello's household token is never written. Held sentences are never written: a G0 hold is logged as its rule id
and the text's sha256 only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts.models.session import SessionMeta

_ID = re.compile(r"^[A-Za-z0-9_-]+$")
BYTE_FIELDS = {"frame": "jpeg_1024", "plate_photo": "jpeg", "utterance": "audio_wav_b64"}


def safe_id(s: str) -> str:
    if not _ID.match(s) or len(s) > 120:
        raise ValueError(f"bad id {s!r}")
    return s


class Recorder:
    def __init__(self, root: Path | None, meta: SessionMeta) -> None:
        self.meta = meta
        self.dir = None if root is None else root / safe_id(meta.session_id)
        self.lines: list[dict[str, Any]] = []
        self._saved_frames: set[str] = set()
        if self.dir is not None:
            (self.dir / "frames").mkdir(parents=True, exist_ok=True)
            (self.dir / "audio").mkdir(exist_ok=True)
            (self.dir / "meta.json").write_text(meta.model_dump_json(indent=1, exclude_none=True))

    def _write(self, line: dict[str, Any]) -> None:
        self.lines.append(line)
        if self.dir is not None:
            with (self.dir / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def client(self, t_ms: float, direction: str, msg: dict[str, Any], stream: str | None = None) -> None:
        kind = str(msg.get("type"))
        field = BYTE_FIELDS.get(kind) if direction == "c2s" else None
        body = {k: v for k, v in msg.items() if k != field}
        if direction == "c2s" and kind == "hello":
            body.pop("household", None)  # the household's bearer token: never written, so never replayed
        if field and msg.get(field):
            body[field] = None  # bytes live in frames/ and audio/
        line: dict[str, Any] = {"t_ms": round(t_ms, 1), "dir": direction, "kind": kind, "msg": body}
        if stream is not None:
            line["stream"] = stream  # s2c: the stream that emitted it (app.session.stream_of)
        self._write(line)

    def state(self, t_ms: float, kind: str, **extra: Any) -> None:
        self._write({"t_ms": round(t_ms, 1), "dir": "state", "kind": kind, **extra})

    def frame(self, frame_id: str, jpeg: bytes) -> None:
        if frame_id in self._saved_frames:
            return
        self._saved_frames.add(frame_id)
        if self.dir is not None:
            (self.dir / "frames" / f"{safe_id(frame_id)}.jpg").write_bytes(jpeg)

    def audio(self, utterance_id: str, wav: bytes) -> None:
        if self.dir is not None:
            (self.dir / "audio" / f"{safe_id(utterance_id)}.wav").write_bytes(wav)

    def end(self, outcome: str | None, t_ms: float | None = None) -> None:
        """The last line: the session's outcome, at the session clock (or, without one, at the last line's time)."""
        if t_ms is None:
            t_ms = max((float(x["t_ms"]) for x in self.lines), default=0.0)
        self.state(t_ms, "end", outcome=outcome)


@dataclass(frozen=True)
class Recording:
    meta: SessionMeta
    lines: list[dict[str, Any]]
    dir: Path

    def s2c(self) -> list[dict[str, Any]]:
        return [x["msg"] for x in self.lines if x["dir"] == "s2c"]

    def s2c_streams(self) -> list[str]:
        """The stream of each s2c message, in s2c order ("main" for a record made before the streams)."""
        return [x.get("stream", "main") for x in self.lines if x["dir"] == "s2c"]

    def frame_bytes(self, frame_id: str) -> bytes | None:
        p = self.dir / "frames" / f"{safe_id(frame_id)}.jpg"
        return p.read_bytes() if p.exists() else None

    def audio_bytes(self, utterance_id: str) -> bytes | None:
        p = self.dir / "audio" / f"{safe_id(utterance_id)}.wav"
        return p.read_bytes() if p.exists() else None


def load(root: Path, session_id: str) -> Recording:
    d = root / safe_id(session_id)
    if not (d / "meta.json").exists():
        raise FileNotFoundError(session_id)
    meta = SessionMeta.model_validate_json((d / "meta.json").read_text())
    path = d / "events.jsonl"
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
    return Recording(meta, lines, d)


def public_lines(rec: Recording) -> list[dict[str, Any]]:
    """The ws lines a replay may show anyone holding its id: a hello without the household's token, which a record made
    before the recorder dropped it still holds."""
    out = []
    for x in rec.lines:
        if x["dir"] not in ("c2s", "s2c"):
            continue
        if x["dir"] == "c2s" and x["kind"] == "hello" and "household" in x["msg"]:
            x = {**x, "msg": {k: v for k, v in x["msg"].items() if k != "household"}}
        out.append(x)
    return out
