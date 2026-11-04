"""Serve the app: the phone's PWA, its /ws and the /api/v1 routes, on loopback behind an HTTPS front door.

    uv run python deploy/serve.py --dist phone/dist [--port 8790]
    tailscale serve --bg --https=8443 http://127.0.0.1:8790        # the phone's HTTPS route

    FIXIT_CALLS=replay uv run python deploy/serve.py --dist phone/dist --port 8080 --replay-only --replays demo/replays

/mcp is the MCP server for agents (integrations/mcp_server), with the caller's bearer token.
Both modes also play the repo's demo recordings (--demo, default demo/replays: ?replay=demo).
--replay-only plays the curated recordings in --replays and answers nothing else (deploy/replay_host.py): no camera
session, no model call, no store. It binds 127.0.0.1 by default; --host is for a container's own address.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.client_gone import ClientGone  # noqa: E402
from deploy.more_replays import DEMO, MoreReplays  # noqa: E402


def served(dist: Path, demo: Path = DEMO) -> Any:
    """The whole app (not --replay-only) as an ASGI app; the Home Assistant add-on serves it too (deploy/ha_addon)."""
    from app.feeds.service import private_root
    from app.schedule.backups import Sweeper
    from app.server import production_app
    from calls import retention
    from deploy.mcp_door import WithMcp, served_store

    Sweeper(private_root()).start()  # store backups go 7 days after they were made
    retention.Sweeper(private_root()).start()  # so do record mode's request bodies and their replies

    # the store, the household routes, the push loop, the read-only HA, the PWA; ?replay=demo; and /mcp
    api = production_app(dist)
    return WithMcp(MoreReplays(api, [demo]), api, served_store)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", type=Path, default=ROOT / "phone/dist")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1; inside a container, its own address")
    ap.add_argument("--replay-only", action="store_true", help="play the recordings in --replays and nothing else")
    ap.add_argument("--replays", type=Path, help="with --replay-only: the curated recordings, one folder each")
    ap.add_argument("--demo", type=Path, default=DEMO, help="the repo's demo recordings, played beside the others")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.replay_only:
        from deploy.replay_host import build

        if args.replays is None:
            raise SystemExit("--replay-only needs --replays DIR")
        app = build(args.replays, args.dist, more=[args.demo])
    else:
        app = served(args.dist, args.demo)
    uvicorn.run(ClientGone(app), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
