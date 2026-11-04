"""More replay folders beside an app's own: /api/v1/replay/{id} (and its frames) for a recording in one of them is
answered from that folder; every other request goes to the app unchanged.

The served app plays the sessions it recorded; the repo's demo/replays (the README's "Try it now", ?replay=demo) lives
elsewhere, so the served app and the replay host both put it here. Each folder is played by a replay-only app (a
replay client, no store, no household routes), and only for the ids found in it at start, and `golden` for the
first folder with a GOLDEN file.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from starlette.types import ASGIApp, Receive, Scope, Send

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo/replays"
REPLAY_PATH = re.compile(r"^/api/v1/replay/([A-Za-z0-9_-]{1,64})(/frames/[A-Za-z0-9_.-]{1,96}\.jpg)?$")


def recordings(replays: Path) -> list[str]:
    return sorted(p.parent.name for p in replays.glob("*/meta.json") if (p.parent / "events.jsonl").is_file())


class MoreReplays:
    def __init__(self, app: ASGIApp, folders: Sequence[Path]) -> None:
        import calls
        from app.server import create_app

        self.app = app
        self.by_id: dict[str, ASGIApp] = {}
        for d in folders:
            if not d.is_dir():
                continue
            player = create_app(client=calls.make_client("replay", private=None), replay_root=d.resolve())
            for rid in recordings(d):
                self.by_id.setdefault(rid, player)
            # deploy/curate.py --golden names the curated recording in its folder; that folder answers `golden`.
            if (d / "GOLDEN").is_file():
                self.by_id.setdefault("golden", player)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in ("GET", "HEAD"):
            m = REPLAY_PATH.match(scope["path"])
            if m and m.group(1) in self.by_id:
                await self.by_id[m.group(1)](scope, receive, send)
                return
        await self.app(scope, receive, send)
