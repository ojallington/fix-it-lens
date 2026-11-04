"""`make demo`: the app in replay on this machine. No key, no camera, no network, no private folder.

    uv run python -m deploy.demo [--port 8080] [--replays demo/replays] [--no-open]

It serves the built PWA (phone/dist) and plays the recordings under --replays at /?replay=<id>; the default opens
/?replay=demo, the replay made by tools/make_demo_replay.py. Model calls can only replay recorded fixtures, so nothing
is spent. It binds 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import calls  # noqa: E402
from app.server import create_app  # noqa: E402
from deploy.pwa import mount_pwa  # noqa: E402

HOST = "127.0.0.1"


def demo_app(replays: Path, dist: Path) -> FastAPI:
    app = create_app(client=calls.make_client("replay", private=None), replay_root=replays.resolve())
    mount_pwa(app, dist)
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--replays", type=Path, default=ROOT / "demo/replays")
    ap.add_argument("--dist", type=Path, default=ROOT / "phone/dist")
    ap.add_argument("--replay", default="demo", help="the recording to open")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    if not (args.dist / "index.html").exists():
        sys.exit(f"{args.dist} has no build; run `cd phone && npm run build` (make demo does)")
    url = f"http://{HOST}:{args.port}/?replay={args.replay}"
    print(f"Fix-It Lens demo: {url}  (replay only; Ctrl-C stops it)", flush=True)
    if not args.no_open:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(demo_app(args.replays, args.dist), host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
