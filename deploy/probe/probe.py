"""The first-day HTTPS probe: does the demo phone get the camera, a service worker and a WebSocket
through `tailscale serve`?

    uv run --with websockets python deploy/probe/probe.py --port 8790
    tailscale serve --bg --https=8443 http://127.0.0.1:8790

Open https://<host>.ts.net:8443/ on the phone and tap Run. Each result is printed here and appended to
--out (one JSON line per run). Binds loopback only; tailscale serve is the front door. Stop it after the check.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).resolve().parent


def make_app(out: Path) -> FastAPI:
    app = FastAPI(title="fixit https probe")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(HERE / "index.html", media_type="text/html")

    @app.get("/sw.js")
    def sw() -> FileResponse:
        return FileResponse(HERE / "sw.js", media_type="text/javascript", headers={"Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest")
    def manifest() -> JSONResponse:
        return JSONResponse({"name": "Fix-It probe", "start_url": "/", "display": "standalone", "icons": []},
                            media_type="application/manifest+json")

    @app.post("/result")
    async def result(request: Request) -> dict[str, bool]:
        body = await request.json()
        line = {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                "ua": request.headers.get("user-agent"), **body}
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as f:
            f.write(json.dumps(line) + "\n")
        print(json.dumps(line), flush=True)
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                msg = await socket.receive_text()
                await socket.send_text(json.dumps({"echo": msg, "server_ms": time.time() * 1000}))
        except WebSocketDisconnect:
            return

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--out", type=Path, default=Path("data/probe/https.jsonl"))
    args = ap.parse_args()
    uvicorn.run(make_app(args.out), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
