"""The public pilot door's route set.

    app = PublicOnly(production_app(dist))   # what `fixit tunnel` serves; the tailnet route serves the app itself

An allowlist: under /api/ only the routes a pilot household uses pass; everything else under /api/ is 404 before
it reaches the app. /preflight and /api/v1/preflight/* are tailnet only (contracts 1.5.0) and /api/v1/replay/* holds
recorded frames from inside homes, so neither is ever routed. Paths outside /api/ are the PWA and /ws.
"""

from __future__ import annotations

import re

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

API = "/api/v1"
PUBLIC_API = re.compile(
    rf"^{API}/("
    r"households|households/door|households/try|households/return|household/return-link|household|household/feeds|feeds/[A-Za-z0-9_-]+\.ics"
    r"|push/vapid-public-key|push/subscriptions|reminders/[^/]+/(?:opened|snooze)"
    r"|appliances|appliances/[^/]+|appliances/[^/]+/(?:card|manual|meaning|display|reports|not-mine|card/record)|appliances/[^/]+/manual/pages/[0-9]+\.png"
    r"|appliances/[^/]+/jobs/[^/]+/(?:last-done|schedule|done|snooze|assignee|changes|undo)"
    r"|household/people|household/people/[^/]+|household/invites|household/join"
    r"|household/tokens|household/tokens/[^/]+|household/feedback|household/feedback/[^/]+/withdraw|me/alerts"
    r"|reports|due|history|sessions|sessions/[^/]+|sessions/[^/]+/note|sessions/[^/]+/pages/\d+\.png"
    r")$"
)
NEVER = re.compile(r"^(/preflight|/api/v1/preflight|/api/v1/replay)(/|$)")


def is_public(path: str) -> bool:
    if NEVER.match(path) or "/../" in path or path.endswith("/.."):
        return False
    if path == "/api" or path.startswith("/api/"):
        return bool(PUBLIC_API.match(path))
    return True


class PublicOnly:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not is_public(scope["path"]):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await PlainTextResponse("Not found", 404)(scope, receive, send)
            return
        await self.app(scope, receive, send)
