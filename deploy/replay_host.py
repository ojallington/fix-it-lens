"""The replay-only host: a link anyone can open to watch curated recordings of the app. It does nothing else.

    uv run python deploy/replay_host.py --replays DIR [--dist phone/dist] [--port 8791] [--default ID]

What it answers (everything else is 404, and every websocket is refused):
- GET / with no ?replay= : a redirect to /?replay=<default> (`golden` when <replays>/GOLDEN names one, so the
  link stays the same when the curated recording changes);
- GET of the PWA's own files under --dist;
- GET /api/v1/replay/{id} and /api/v1/replay/{id}/frames/{frame}.jpg, for the recordings in --replays and in
  demo/replays (?replay=demo) only.

So: no camera session (no /ws), no model call and no key (FIXIT_CALLS must be replay), nothing spent, no store and
no household data (none is opened; the private folder is an empty temporary one), no /preflight, no API docs. Each
client address gets RATE requests a minute, then 429. It binds 127.0.0.1; `tailscale serve` is the tailnet door,
and a Cloudflare tunnel the public one (deploy/replay_door.py).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from starlette.types import ASGIApp, Message, Receive, Scope, Send

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RATE_PER_MIN = 600  # a replay loads its events and one jpg per frame: a few hundred requests, once
REPLAY_API = re.compile(r"^/api/v1/replay/[A-Za-z0-9_-]{1,64}(/frames/[A-Za-z0-9_.-]{1,96}\.jpg)?$")
HEADERS = [(b"x-robots-tag", b"noindex"), (b"x-content-type-options", b"nosniff"),
           (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY")]


def recordings(replays: Path) -> list[str]:
    return sorted(p.parent.name for p in replays.glob("*/meta.json") if (p.parent / "events.jsonl").is_file())


def golden(replays: Path) -> str | None:
    """The recording `golden` names, by the app's rule (app/config.golden_replay): FIXIT_REPLAY_GOLDEN, else the
    one line in <replays>/GOLDEN."""
    rid = os.environ.get("FIXIT_REPLAY_GOLDEN")
    if not rid and (replays / "GOLDEN").is_file():
        rid = (replays / "GOLDEN").read_text().strip()
    return rid or None


class Bucket:
    def __init__(self, per_min: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.cap, self.rate, self.clock = float(per_min), per_min / 60.0, clock
        self.left: dict[str, tuple[float, float]] = {}

    def take(self, who: str, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        if len(self.left) > 10_000:
            self.left.clear()
        tokens, t = self.left.get(who, (self.cap, now))
        tokens = min(self.cap, tokens + (now - t) * self.rate)
        ok = tokens >= 1.0
        self.left[who] = (tokens - 1.0 if ok else tokens, now)
        return ok


class ReplayOnly:
    """The allowlist in front of the app; nothing reaches the app that a replay does not need."""

    def __init__(self, app: ASGIApp, dist: Path, default: str, *, per_min: int = RATE_PER_MIN,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.app, self.dist, self.default = app, dist.resolve(), default
        self.bucket = Bucket(per_min, clock)

    def allowed(self, path: str) -> bool:
        if path == "/" or REPLAY_API.match(path):
            return True
        if path.startswith(("/api", "/preflight", "/ws", "/docs", "/redoc", "/openapi")):
            return False
        f = (self.dist / path.lstrip("/")).resolve()
        return f.is_file() and f.is_relative_to(self.dist)

    async def _plain(self, send: Send, status: int, body: bytes, extra: list[tuple[bytes, bytes]] = ()) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"), *HEADERS, *extra]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "replays only"})
            return
        path, method = scope["path"], scope["method"]
        who = (scope.get("client") or ("?", 0))[0]
        if not self.bucket.take(who):
            await self._plain(send, 429, b"Too many requests; try again in a minute.\n", [(b"retry-after", b"60")])
            return
        if method != "GET" or not self.allowed(path):
            await self._plain(send, 404, b"Not here: this host only plays recordings.\n")
            return
        if path == "/" and b"replay=" not in scope.get("query_string", b""):
            loc = f"/?replay={self.default}".encode()
            await send({"type": "http.response.start", "status": 302, "headers": [(b"location", loc), *HEADERS]})
            await send({"type": "http.response.body", "body": b""})
            return

        async def with_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": [*msg.get("headers", []), *HEADERS]}
            await send(msg)

        await self.app(scope, receive, with_headers)


def build(replays: Path, dist: Path, default: str | None = None, *, per_min: int = RATE_PER_MIN,
          more: Sequence[Path] = (), clock: Callable[[], float] = time.monotonic) -> ReplayOnly:
    """`more`: other folders of curated recordings played beside --replays (the repo's demo/replays)."""
    if os.environ.get("FIXIT_CALLS", "replay") != "replay":
        raise SystemExit("the replay host runs in replay mode only: unset FIXIT_CALLS or set it to replay")
    os.environ["FIXIT_CALLS"] = "replay"
    # for the whole process, on purpose: nothing private is ever read (a test puts both back with monkeypatch)
    os.environ["FIXIT_PRIVATE"] = tempfile.mkdtemp(prefix="fixit-replay-host-")
    ids = recordings(replays)
    if not ids:
        raise SystemExit(f"{replays}: no recordings (each is a folder with meta.json and events.jsonl)")
    gold = golden(replays)
    if gold is not None and gold not in ids:  # here `golden` may only ever name a curated recording
        raise SystemExit(f"golden names {gold}, which is not a recording in {replays} ({', '.join(ids)})")
    default = default or ("golden" if gold else ids[0])
    if default == "golden" and gold is None:
        raise SystemExit(f"--default golden: no {replays}/GOLDEN and no FIXIT_REPLAY_GOLDEN")
    if default != "golden" and default not in ids:
        raise SystemExit(f"--default {default}: not in {replays} ({', '.join(ids)})")
    import calls
    from app.server import create_app
    from deploy.pwa import mount_pwa

    app = create_app(client=calls.make_client("replay", private=Path(os.environ["FIXIT_PRIVATE"])),
                     replay_root=replays.resolve())
    mount_pwa(app, dist)
    from deploy.more_replays import MoreReplays

    others = [d for d in more if d.resolve() != replays.resolve()]
    return ReplayOnly(MoreReplays(app, others) if others else app, dist, default, per_min=per_min, clock=clock)


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--replays", type=Path, required=True, help="the curated public recordings, one folder each")
    ap.add_argument("--dist", type=Path, default=ROOT / "phone/dist")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--demo", type=Path, default=ROOT / "demo/replays", help="the repo's demo recordings, played too")
    ap.add_argument("--default", help="the recording / opens (default: golden if set, else the first by name)")
    args = ap.parse_args()
    host = build(args.replays, args.dist, args.default, more=[args.demo])
    print(f"replay host: {', '.join(recordings(args.replays))}; / opens {host.default}", flush=True)
    uvicorn.run(host, host="127.0.0.1", port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
