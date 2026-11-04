"""/mcp on the app's own door: streamable HTTP (stateless, JSON answers), the caller's bearer token passed through to
the app's /api/v1 in process, so an agent's call is the same request a curl would make with that token.

A request without a bearer token is a 401 before MCP sees it. Links point at the address the request came in on.
"""

from __future__ import annotations

import functools
import sqlite3
from collections.abc import Callable

import httpx
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from app.schedule.store import token_scope
from integrations.mcp_server.server import Headers, build

INTERNAL = "http://app.internal"  # the in-process base URL; it never leaves this process


def _bearer(h: Headers) -> str | None:
    auth = (h or {}).get("authorization", "")
    return auth[7:].strip() or None if auth.lower().startswith("bearer ") else None


def _origin(h: Headers) -> str:
    h = h or {}
    proto = h.get("x-forwarded-proto", "https").split(",")[0].strip()
    host = h.get("x-forwarded-host") or h.get("host", "localhost")
    return f"{proto}://{host.split(',')[0].strip()}"


class WithMcp:
    """`app` with /mcp beside it. Everything else goes to `app` untouched, lifespan included."""

    def __init__(self, app: ASGIApp, api: ASGIApp, store: Callable[[], tuple[sqlite3.Connection, bytes]]) -> None:
        self.app = app

        def rest(h: Headers) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url=INTERNAL, timeout=20,
                                     headers={"Authorization": f"Bearer {_bearer(h)}"})

        def scope_of(h: Headers) -> str | None:
            token = _bearer(h)
            return token_scope(*store(), token) if token else None

        self.mcp = build(rest, _origin, scope_of).streamable_http_app(  # type: ignore[arg-type]
            streamable_http_path="/mcp", stateless_http=True, json_response=True,
            # the Host check guards a server that has no auth on localhost; this one is reached through the app's
            # own door under its public name, and every request carries a bearer token
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            async with self.mcp.router.lifespan_context(self.mcp):
                await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope["path"] in ("/mcp", "/mcp/"):
            if not _bearer({k.decode().lower(): v.decode() for k, v in scope["headers"]}):
                await _unauthorized(send)
                return
            await self.mcp(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _unauthorized(send: Send) -> None:
    body = b'{"error":"a bearer token is required: the household token, or one it issued to this agent"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
    await send({"type": "http.response.body", "body": body})




@functools.cache
def served_store() -> tuple[sqlite3.Connection, bytes]:
    """The served store and token secret, opened on the first /mcp request (read by path, as the app does)."""
    from app.feeds.service import private_root
    from app.schedule.invites import db_path
    from app.schedule.store import load_secret, open_store

    return open_store(db_path()), load_secret(private_root() / "secrets/household.secret")
