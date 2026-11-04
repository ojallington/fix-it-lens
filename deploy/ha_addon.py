"""Fix-It Lens as a Home Assistant add-on (integrations/homeassistant/addon/fixit_lens): the add-on's start.

    python deploy/ha_addon.py        # the add-on image's CMD; it reads /data/options.json

Two servers in one process:
- the app on APP_PORT (8790), as `deploy/serve.py` serves it: the phone's PWA, /api/v1, /ws, /mcp. Home Assistant
  reaches it on the add-on's own hostname; the phone, at the address the owner set (`phone_url`), over HTTPS with the
  certificate Home Assistant already has (`ssl`), because the camera and the service worker need HTTPS.
- the panel on PANEL_PORT (8099), shown in Home Assistant's sidebar through ingress: what runs, the phone's address
  and a QR code to join, one new invite at a time, and a "Connect to Home Assistant" button per home. It answers only
  the Supervisor's ingress proxy, which lets through only someone logged in to Home Assistant.

"Connect to Home Assistant" makes a key of kind `ha` for that home and hands it to Home Assistant through the
Supervisor's discovery, so the integration offers itself under Settings, Devices & services with nothing to paste.
The key is never shown, logged or written here; if the Supervisor refuses it, the key is revoked at once. The add-on
calls no Home Assistant service and reads no device: the Supervisor's token is used for discovery only.

/data is the private folder (the store, its secret, push's key), kept by the Supervisor and in its backups. Replay mode
is the default: no key, nothing spent. Live mode reads the Meta key by path, from the add-on's config folder.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import socket
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import segno
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.feeds.push import DEFAULT_VAPID, load_vapid  # noqa: E402
from app.schedule import store  # noqa: E402
from app.schedule.invites import db_path  # noqa: E402

log = logging.getLogger("fixit.ha_addon")

OPTIONS = Path("/data/options.json")
SSL_DIR = Path("/ssl")                       # Home Assistant's certificates (map: ssl:ro)
ADDON_CONFIG = Path("/config")               # the add-on's own config folder (map: addon_config)
KEY_NAME = "meta-model-api.key"              # live mode's key, put there by the owner, read by path
INGRESS_PEER = "172.30.32.2"                 # the Supervisor's ingress proxy, the only caller of the panel
SUPERVISOR = "http://supervisor"
APP_PORT, PANEL_PORT = 8790, 8099
SERVICE = "fixit_lens"                       # the discovery service: the integration's domain
TOKEN_LABEL = "Home Assistant (add-on)"


@dataclass(frozen=True)
class Options:
    calls: Literal["replay", "live"] = "replay"
    phone_url: str | None = None             # https://… the phone opens; None until the owner sets it
    ssl: bool = False
    certfile: str = "fullchain.pem"
    keyfile: str = "privkey.pem"


def read_options(path: Path = OPTIONS) -> Options:
    """The owner's options (the Supervisor checks their types against config.yaml first); a bad value stops here."""
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    calls = raw.get("calls", "replay")
    if calls not in ("replay", "live"):
        raise SystemExit(f"calls must be replay or live, not {calls!r}")
    url = str(raw.get("phone_url") or "").strip().rstrip("/") or None
    if url is not None and not url.startswith("https://"):
        raise SystemExit("phone_url must start with https:// (the phone's camera needs HTTPS)")
    for name in ("certfile", "keyfile"):
        if "/" in str(raw.get(name, "")) or str(raw.get(name, "")).startswith("."):
            raise SystemExit(f"{name} is a file name in Home Assistant's ssl folder, not a path")
    return Options(calls=calls, phone_url=url, ssl=bool(raw.get("ssl", False)),
                   certfile=str(raw.get("certfile") or Options.certfile),
                   keyfile=str(raw.get("keyfile") or Options.keyfile))


def live_key(home: Path, config: Path = ADDON_CONFIG) -> bool:
    """Live mode's key: the owner's file in the add-on's config folder, linked where config/models.toml reads it
    (~/.config/fleet/). Never copied, never read here. False when it is not there."""
    src = config / KEY_NAME
    if not src.is_file():
        return False
    dst = home / ".config/fleet" / KEY_NAME
    dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src)
    return True


# ---- the panel ---------------------------------------------------------------------------------------------------

def _homes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The joined homes in this store, with how many appliances and Home Assistant keys each has."""
    return conn.execute(
        "SELECT h.id, h.name, h.created_at,"
        " (SELECT count(*) FROM appliances a WHERE a.household_id = h.id AND a.deleted_at IS NULL) AS appliances,"
        " (SELECT count(*) FROM household_tokens t WHERE t.household_id = h.id AND t.by_kind = 'ha'"
        "  AND t.revoked_at IS NULL) AS ha_keys"
        " FROM households h WHERE h.consent_at IS NOT NULL AND h.deleted_at IS NULL ORDER BY h.created_at").fetchall()


def _qr(link: str) -> str:
    return segno.make(link, error="m").svg_inline(scale=5, dark="#111", light="#fff")


STYLE = """
:root { color-scheme: light dark; --bg: #fafaf7; --fg: #1b1b18; --muted: #6b6b63; --line: #deded6; --ok: #1f6f43;
        --warn: #8a5a00; --btn: #1b1b18; --btn-fg: #fafaf7; }
@media (prefers-color-scheme: dark) { :root { --bg: #161614; --fg: #ecece6; --muted: #a1a198; --line: #34342f;
        --ok: #6cc596; --warn: #e2b04a; --btn: #ecece6; --btn-fg: #161614; } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; } h2 { font-size: 1.1rem; margin: 32px 0 8px; }
p { margin: 8px 0; } .muted { color: var(--muted); } .ok { color: var(--ok); } .warn { color: var(--warn); }
section { border-top: 1px solid var(--line); }
table { width: 100%; border-collapse: collapse; } td, th { text-align: left; padding: 8px 4px;
        border-bottom: 1px solid var(--line); vertical-align: top; }
button { font: inherit; padding: 6px 14px; border-radius: 6px; border: 0; background: var(--btn); color: var(--btn-fg);
         cursor: pointer; }
code { font-size: .95em; overflow-wrap: anywhere; } .qr svg { width: 200px; height: 200px; } form { display: inline; }
.notice { border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; margin: 16px 0; }
"""


def _page(base: str, opts: Options, homes: list[sqlite3.Row], *, notice: str = "", app_url: str) -> str:
    e = html.escape
    mode = ("Replay mode: the recorded sessions, no key, nothing spent." if opts.calls == "replay" else
            "Live mode: sessions call Meta's models with your key, inside each home's daily cap.")
    if opts.phone_url:
        phone = (f"<p>Your phone opens <code>{e(opts.phone_url)}</code>. Make an invite below and scan its code with "
                 "the phone's camera.</p>")
    else:
        phone = ("<p class='warn'>No phone address yet.</p><p>The phone needs an HTTPS address it can reach, because "
                 "the camera and reminders need HTTPS. In this add-on's Configuration, set <b>phone_url</b> (for "
                 "example <code>https://homeassistant.example.org:8790</code>), turn on <b>ssl</b> with the "
                 "certificate Home Assistant already uses, and give port 8790 a host port under Network. "
                 "The Documentation tab explains it.</p>")
    rows = "".join(
        f"<tr><td>{e(h['name'] or 'Home')}<br><span class='muted'>joined {e(h['created_at'][:10])}</span></td>"
        f"<td>{h['appliances']}</td>"
        f"<td>{'<span class=ok>connected</span>' if h['ha_keys'] else '—'}</td>"
        f"<td><form method='post' action='{e(base)}/connect'><input type='hidden' name='household' "
        f"value='{e(h['id'])}'><button>{'Connect again' if h['ha_keys'] else 'Connect to Home Assistant'}"
        "</button></form></td></tr>" for h in homes)
    table = (f"<table><tr><th>Home</th><th>Appliances</th><th>Home Assistant</th><th></th></tr>{rows}</table>"
             if homes else "<p class='muted'>No home has joined yet. Make an invite and join from the phone.</p>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Fix-It Lens</title><style>{STYLE}</style>
</head><body><main>
<h1>Fix-It Lens</h1><p class="muted">{e(mode)}</p>{notice}
<section><h2>The phone</h2>{phone}
<form method="post" action="{e(base)}/invite"><button>Make an invite</button></form>
<p class="muted">Each invite lets one new home join, once.</p></section>
<section><h2>Homes</h2>{table}
<p class="muted">Connecting makes a key for that home and hands it to Home Assistant, which then offers Fix-It Lens
under Settings, Devices &amp; services. The key can see the home's upkeep jobs and mark them done or snoozed. It never
controls a device. Remove it in the app under About, Connections.</p></section>
<section><h2>How Home Assistant reaches it</h2><p class="muted">Inside Home Assistant, at <code>{e(app_url)}</code>.
Nothing here calls a Home Assistant service or reads a device.</p></section>
</main></body></html>"""


class Supervisor:
    """The Supervisor's API, for discovery only (SUPERVISOR_TOKEN, which the Supervisor gives the add-on)."""

    def __init__(self, token: str | None, http: httpx.Client | None = None) -> None:
        self.token = token
        self.http = http or httpx.Client(base_url=SUPERVISOR, timeout=10.0)

    def discover(self, config: dict[str, str]) -> bool:
        if not self.token:
            return False
        try:
            r = self.http.post("/discovery", json={"service": SERVICE, "config": config},
                               headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError:
            log.warning("discovery: the Supervisor did not answer")
            return False
        if r.status_code != 200:
            log.warning("discovery: the Supervisor refused it (%d)", r.status_code)
        return r.status_code == 200


def panel(conn: sqlite3.Connection, opts: Options, supervisor: Supervisor, app_url: str, *,
          peer: str = INGRESS_PEER) -> FastAPI:
    """The sidebar panel. Every request not from the ingress proxy is refused."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def ingress_only(request: Request, call_next: Any) -> Response:
        if request.client is None or request.client.host != peer:
            return PlainTextResponse("Open Fix-It Lens from Home Assistant's sidebar.", status_code=403)
        response: Response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = ("default-src 'none'; style-src 'unsafe-inline'; "
                                                       "img-src data:; form-action 'self'; frame-ancestors 'self'")
        return response

    def base(request: Request) -> str:
        return request.headers.get("x-ingress-path", "").rstrip("/")

    def show(request: Request, notice: str = "", status: int = 200) -> HTMLResponse:
        return HTMLResponse(_page(base(request), opts, _homes(conn), notice=notice, app_url=app_url),
                            status_code=status)

    @app.get("/")
    def index(request: Request) -> HTMLResponse:
        return show(request)

    @app.post("/invite")
    def invite(request: Request) -> HTMLResponse:
        [inv] = store.create_invites(conn, 1)
        code = f"{inv.code[:5]}-{inv.code[5:]}"
        log.info("panel: one invite made")
        if opts.phone_url is None:
            body = (f"<p>Invite <b><code>{code}</code></b>. It works once. Set the phone's address first (below), "
                    "then type this code in the app.</p>")
        else:
            link = f"{opts.phone_url}/?invite={code}"
            body = (f"<p>Scan this with the phone's camera to join. It works once.</p><div class='qr'>{_qr(link)}"
                    f"</div><p><code>{html.escape(link)}</code></p><p class='muted'>Invite {code}. Shown once.</p>")
        return show(request, f"<div class='notice'>{body}</div>")

    @app.post("/connect")
    def connect(request: Request, household: str = Form(...)) -> HTMLResponse:
        if not any(h["id"] == household for h in _homes(conn)):
            return show(request, "<div class='notice warn'>That home is not here.</div>", 404)
        tid, token = store.issue_token(conn, household, by_kind="ha", scope="write", label=TOKEN_LABEL)
        if not supervisor.discover({"url": app_url, "token": token}):
            store.revoke_token(conn, household, tid)
            return show(request, "<div class='notice warn'>Home Assistant could not be told; no key was kept. "
                                 "Try again, or add Fix-It Lens by hand under Settings, Devices &amp; services."
                                 "</div>", 502)
        log.info("panel: Home Assistant key %s made for a home and handed to discovery", tid)
        return show(request, "<div class='notice ok'>Done. Home Assistant now offers Fix-It Lens under Settings, "
                             "Devices &amp; services (Discovered). Press Add there. If the Fix-It Lens integration is "
                             "not installed yet, see the Documentation tab.</div>")

    return app


# ---- start -------------------------------------------------------------------------------------------------------

def app_url(hostname: str | None = None) -> str:
    """Where Home Assistant reaches the app: the add-on's hostname on the Supervisor's network."""
    return f"http://{hostname or socket.gethostname()}:{APP_PORT}"


async def _serve(servers: list[uvicorn.Server]) -> None:
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    opts = read_options()
    os.environ["FIXIT_CALLS"] = opts.calls
    if opts.phone_url:
        os.environ.setdefault("FIXIT_URL", opts.phone_url)
    if opts.calls == "live" and not live_key(Path.home()):
        raise SystemExit(f"live mode needs {KEY_NAME} in this add-on's config folder (the Documentation tab)")
    ssl: dict[str, str] = {}
    if opts.ssl:
        cert, key = SSL_DIR / opts.certfile, SSL_DIR / opts.keyfile
        if not (cert.is_file() and key.is_file()):
            raise SystemExit(f"ssl is on, but {cert} or {key} is not there")
        ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    conn = store.open_store(db_path())
    load_vapid(Path(os.environ.get("FIXIT_VAPID", DEFAULT_VAPID)), create=True)  # push's key, made once (0600)

    from deploy.client_gone import ClientGone
    from deploy.serve import served

    # Inside the add-on's container, its own network: the Supervisor's network and the host port the owner maps.
    host = "0.0.0.0"  # noqa: S104
    the_app = uvicorn.Server(uvicorn.Config(ClientGone(served(ROOT / "phone/dist")), host=host, port=APP_PORT,
                                            proxy_headers=False, **ssl))  # type: ignore[arg-type]
    the_panel = uvicorn.Server(uvicorn.Config(panel(conn, opts, Supervisor(os.environ.get("SUPERVISOR_TOKEN")),
                                                    app_url()), host=host, port=PANEL_PORT, proxy_headers=False))
    log.info("Fix-It Lens: the app on %d%s, the panel in the sidebar; %s mode", APP_PORT,
             " (HTTPS)" if ssl else "", opts.calls)
    asyncio.run(_serve([the_app, the_panel]))


if __name__ == "__main__":
    main()
