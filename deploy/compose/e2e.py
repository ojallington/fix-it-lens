"""End-to-end check of a running compose stack, from inside the app's network namespace (no port is published):

    docker compose exec app python deploy/compose/e2e.py

Replay mode with no key: the PWA, /preflight, an invite joined, push's key, the household's due list and .ics
feed, a session opened over /ws, and a recording played back. It writes one test household and one recording named
e2e-replay into /data, so run it on a scratch stack (`docker compose down -v` after).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.recorder import Recorder  # noqa: E402
from app.schedule import store  # noqa: E402
from app.schedule.invites import db_path  # noqa: E402
from contracts.models.session import SessionMeta  # noqa: E402

BASE = "http://127.0.0.1:" + os.environ.get("FIXIT_PORT", "8790")
failed: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + str(detail) if detail != '' else ''}", flush=True)
    if not ok:
        failed.append(name)


def hello(**kw: object) -> str:
    return json.dumps({"type": "hello", "seq": 0, "t_ms": 0, "session_id": None, "last_seq": None, "client": "phone",
                       "lang": "en", **kw})


async def ws_checks(token: str) -> None:
    url = BASE.replace("http", "ws") + "/ws"
    async with websockets.connect(url + "?mode=capture") as w:
        await w.send(hello(household=token))
        ok = json.loads(await w.recv())
        check("ws: a session opens for the household", ok.get("type") == "hello_ok", ok.get("type"))
    meta = SessionMeta(session_id="e2e-replay", started_at=datetime.now(UTC), mode="capture", lang="en",
                       contracts_version="1.8.0")
    rec = Recorder(Path(os.environ["FIXIT_PRIVATE"]) / "sessions", meta)
    rec.client(0, "s2c", {"type": "hello_ok", "seq": 1, "t_ms": 0, "session_id": "e2e-replay"})
    rec.client(10, "s2c", {"type": "say", "seq": 2, "t_ms": 10, "text": "recorded line"})
    rec.end("done")
    async with websockets.connect(url + "?speed=50") as w:
        await w.send(hello(replay_id="e2e-replay"))
        got = [json.loads(await asyncio.wait_for(w.recv(), 5)) for _ in range(2)]
        check("ws: a recording replays with its badge", [m["type"] for m in got] == ["replay", "say"]
              and got[0].get("badge") is True, [m["type"] for m in got])


def main() -> None:
    key = Path("~/.config/fleet/meta-model-api.key").expanduser()
    check("replay mode", os.environ.get("FIXIT_CALLS") == "replay", os.environ.get("FIXIT_CALLS"))
    keys = [p for d in ("/app", "/data") for p in Path(d).rglob("*.key")]
    check("no key in the container", not key.exists() and not keys, keys or key)
    h = httpx.Client(base_url=BASE, timeout=10)
    for path, kind in (("/", "text/html"), ("/sw.js", "javascript"), ("/preflight", "text/html")):
        r = h.get(path)
        check(f"GET {path}", r.status_code == 200 and kind in r.headers.get("content-type", ""), r.status_code)
    lines = h.get("/api/v1/preflight").json()
    for line in lines:
        print(f"     preflight {'ok ' if line['ok'] else 'red'} {line['check']}: {line['detail']}")
    calls = next((x for x in lines if x["check"] == "calls"), None)
    check("preflight calls: replay, nothing spent", bool(calls and calls["ok"] and "replay" in calls["detail"]))
    conn = store.open_store(db_path())
    [inv] = store.create_invites(conn, 1)
    r = h.post("/api/v1/households", json={"invite_code": inv.code, "consent": True})
    check("join with an invite", r.status_code == 201, r.status_code)
    token = r.json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    check("the invite works once", h.post("/api/v1/households", json={"invite_code": inv.code, "consent": True})
          .status_code == 403)
    check("GET /household", h.get("/api/v1/household", headers=auth).status_code == 200)
    vapid = h.get("/api/v1/push/vapid-public-key", headers=auth)
    check("push is on (VAPID key made at first start)", vapid.status_code == 200, vapid.status_code)
    check("no token: 401", h.get("/api/v1/due").status_code == 401)
    due = h.get("/api/v1/due", headers=auth)
    check("GET /due", due.status_code == 200 and isinstance(due.json(), list), due.status_code)
    ics = h.get(h.get("/api/v1/household/feeds", headers=auth).json()["ics_url"].replace("https://", "http://"))
    check("the .ics feed", ics.status_code == 200 and ics.text.startswith("BEGIN:VCALENDAR"), ics.status_code)
    asyncio.run(ws_checks(token))
    print("e2e:", "all passed" if not failed else f"{len(failed)} failed: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
