"""A websocket client that goes away mid-handshake is logged as one line, not a traceback.

A phone that loses signal, or a probe that gives up, before /ws has its hello: the app's close(1008) then finds the
connection gone, and the send's failure (uvicorn's ClientDisconnected, which starlette re-raises as
WebSocketDisconnect 1006) leaves the app. Nothing is left to answer, so it is logged and
dropped here; any other error, and every HTTP request, passes through unchanged.
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect
from uvicorn.protocols.utils import ClientDisconnected

log = logging.getLogger("fixit.ws")


class ClientGone:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except (ClientDisconnected, WebSocketDisconnect):
            log.info("%s: the client went away before it was answered", scope["path"])
