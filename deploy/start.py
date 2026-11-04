"""The app's start in the compose stack (compose.yaml): the address, a QR for the phone, then deploy/serve.py.

    docker compose up      # runs: python deploy/start.py

The address is the tailnet name the sidecar got (its local API, through the shared socket), or FIXIT_URL. The first
time, when the store has no household, it makes one invite for this home and the QR opens it: scan it, join, done;
and push's VAPID key (FIXIT_VAPID, in /data/secrets) once.
Codes print once; later ones come from `docker compose exec app python -m app.schedule.invites new 1 --link URL`.
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import segno

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.feeds.push import DEFAULT_VAPID, load_vapid  # noqa: E402
from app.schedule import store  # noqa: E402
from app.schedule.invites import db_path  # noqa: E402

TS_SOCKET = Path(os.environ.get("TS_SOCKET", "/var/run/tailscale/tailscaled.sock"))
WAIT_S = 90.0  # the sidecar's first login


def tailnet_url(sock: Path, *, wait_s: float = WAIT_S, transport: httpx.BaseTransport | None = None) -> str | None:
    """https://<name>.<tailnet>.ts.net once the sidecar is logged in, else None after wait_s."""
    if transport is None and not sock.exists():
        return None
    http = httpx.Client(transport=transport or httpx.HTTPTransport(uds=str(sock)), timeout=5.0)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            st = http.get("http://local-tailscaled.sock/localapi/v0/status").json()
            name = str((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
            if st.get("BackendState") == "Running" and name:
                return f"https://{name}"
        except (httpx.HTTPError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(2.0)


def first_invite(conn: sqlite3.Connection) -> str | None:
    """One invite for this home, the first time only (no household in the store yet)."""
    if conn.execute("SELECT 1 FROM households LIMIT 1").fetchone():
        return None
    [inv] = store.create_invites(conn, 1)
    return f"{inv.code[:5]}-{inv.code[5:]}"


def banner(url: str | None, code: str | None) -> str:
    invite = f"Invite {code} (this home; it works once)\n" if code else ""
    if url is None:
        return ("Fix-It Lens is up, but it has no HTTPS address yet: the sidecar is not logged in "
                "(deploy/compose/README.md), or set FIXIT_URL.\n" + invite)
    link = f"{url}/?invite={code}" if code else url
    buf = io.StringIO()
    segno.make(link, error="m").terminal(out=buf, compact=True)
    head = f"Fix-It Lens: {url}\n" + ("Scan this with your phone's camera to join:\n" if code
                                      else "Open it on your phone:\n")
    tail = f"{link}\n{invite}" if code else "Already set up: no new invite.\n"
    return head + buf.getvalue() + tail


def main() -> None:
    conn = store.open_store(db_path())
    code = first_invite(conn)
    conn.close()
    load_vapid(Path(os.environ.get("FIXIT_VAPID", DEFAULT_VAPID)), create=True)  # push's key, made once (0600)
    url = os.environ.get("FIXIT_URL") or tailnet_url(TS_SOCKET)
    print(banner(url, code), flush=True)
    from deploy import serve

    sys.argv = ["serve", "--dist", str(ROOT / "phone/dist"), "--port", os.environ.get("FIXIT_PORT", "8790")]
    serve.main()


if __name__ == "__main__":
    main()
