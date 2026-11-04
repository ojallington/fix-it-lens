"""The clip labelling tool: a small local web page over eval/label/store.py.

    make label                                   # http://127.0.0.1:8765, reads FIXIT_PRIVATE
                                                 # /latency: the hazard read-off (eval/hazard.py), frame by frame
    python -m eval.label.server --set power      # label another clip set (eval.stepcheck.results.SETS)

Binds to 127.0.0.1 only: the frames are private until consent and the scan are done.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from eval.label import readoff
from eval.label.store import LabelError, save_label, session_frames, state
from eval.stepcheck.results import SETS, ClipSet

PAGE = Path(__file__).resolve().parent / "index.html"
LATENCY_PAGE = Path(__file__).resolve().parent / "latency.html"


def private_dir() -> Path:
    env = os.environ.get("FIXIT_PRIVATE")
    p = Path(env) if env else Path.home() / "Projects/hackathons/fix-it-lens-private"
    if not p.is_dir():
        raise SystemExit(f"no private folder at {p}; set FIXIT_PRIVATE")
    return p


class Handler(BaseHTTPRequestHandler):
    private: Path
    clip_set: ClipSet = SETS["main"]

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        parts = [unquote(p) for p in self.path.split("?")[0].strip("/").split("/") if p]
        try:
            if not parts:
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif parts == ["latency"]:
                self._send(200, LATENCY_PAGE.read_bytes(), "text/html; charset=utf-8")
            elif parts == ["api", "latency", "state"]:
                self._json(readoff.state(self.private))
            elif len(parts) == 3 and parts[:2] == ["clip", "hazard"]:
                self._video(readoff.clip_path(self.private, parts[2]))
            elif parts == ["api", "state"]:
                self._json(state(self.private, self.clip_set.plan, self.clip_set.labels))
            elif len(parts) == 3 and parts[:1] == ["api"] and parts[2] == "frames":
                frames = session_frames(self.private, parts[1])
                self._json([{"frame_id": f.frame_id, "t_ms": f.t} for f in frames])
            elif len(parts) == 3 and parts[0] == "frame":
                match = [f for f in session_frames(self.private, parts[1]) if f.frame_id == parts[2]]
                if not match:
                    self._json({"error": "no such frame"}, HTTPStatus.NOT_FOUND)
                else:
                    self._send(200, match[0].path.read_bytes(), "image/jpeg")
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except LabelError as e:
            self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

    def _video(self, path: Path) -> None:
        """The clip, with byte ranges, so the page can seek frame by frame."""
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = min(int(b), size - 1) if a and b else end
        if start > end or start >= size:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(HTTPStatus.PARTIAL_CONTENT if rng else HTTPStatus.OK)
        self.send_header("Content-Type", {".webm": "video/webm", ".mov": "video/quicktime",
                                          ".mkv": "video/x-matroska"}.get(path.suffix.lower(), "video/mp4"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0 and (chunk := f.read(min(left, 1 << 20))):
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/latency/reading":
            n = int(self.headers.get("Content-Length", "0"))
            try:
                self._json(readoff.save_reading(self.private, json.loads(self.rfile.read(n))))
            except (LabelError, json.JSONDecodeError) as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path != "/api/label":
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        n = int(self.headers.get("Content-Length", "0"))
        try:
            line = save_label(self.private, json.loads(self.rfile.read(n)), plan_path=self.clip_set.plan,
                              labels_path=self.clip_set.labels)
        except (LabelError, json.JSONDecodeError) as e:
            self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return
        self._json(line)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.label.server")
    ap.add_argument("port", type=int, nargs="?", default=8765)
    ap.add_argument("--set", choices=list(SETS), default="main")
    a = ap.parse_args(argv)
    Handler.private = private_dir()
    Handler.clip_set = SETS[a.set]
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"labelling tool ({a.set} set) on http://127.0.0.1:{a.port} (private folder {Handler.private})")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
