"""Serve the built PWA (phone/dist) at / of the app's own origin, beside /api/v1 and /ws.

    from deploy.pwa import mount_pwa
    mount_pwa(app, Path("phone/dist"))   # after the API routes, so they win

sw.js and index.html are no-cache (a new build reaches the phone on the next load); hashed /assets and the
MediaPipe /mp files are cached for a year and gzip-compressed on the way out. /preflight is the phone's preflight
page (deploy/preflight.html: the server's lines plus rtt, battery, camera, service worker).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

PREFLIGHT = Path(__file__).with_name("preflight.html")
LONG_CACHE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"


class _Cached(StaticFiles):
    def __init__(self, directory: Path, cache: str) -> None:
        super().__init__(directory=directory)
        self.cache = cache

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_cached(message: dict) -> None:  # type: ignore[type-arg]
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"cache-control", self.cache.encode()))
            await send(message)

        await super().__call__(scope, receive, send_cached)


def mount_pwa(app: FastAPI, dist: Path) -> None:
    dist = dist.resolve()
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    for sub in ("assets", "mp"):
        if (dist / sub).is_dir():
            app.mount(f"/{sub}", _Cached(dist / sub, LONG_CACHE), name=f"pwa-{sub}")

    @app.get("/sw.js", include_in_schema=False)
    def sw() -> FileResponse:
        return FileResponse(dist / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": NO_CACHE, "Service-Worker-Allowed": "/"})

    @app.get("/preflight", include_in_schema=False)
    def preflight_page() -> FileResponse:
        return FileResponse(PREFLIGHT, media_type="text/html", headers={"Cache-Control": NO_CACHE})

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        f = (dist / path).resolve()
        if path and f.is_file() and f.is_relative_to(dist):
            return FileResponse(f, headers={"Cache-Control": NO_CACHE})
        return FileResponse(dist / "index.html", media_type="text/html", headers={"Cache-Control": NO_CACHE})
