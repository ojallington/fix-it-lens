"""deploy/start.py: the address from the sidecar, one invite the first time, and the QR banner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx

from app.schedule import store
from deploy import start


def status(state: str, name: str) -> httpx.MockTransport:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/localapi/v0/status" and req.method == "GET"
        return httpx.Response(200, json={"BackendState": state, "Self": {"DNSName": name}})
    return httpx.MockTransport(h)


def test_the_tailnet_name_once_logged_in() -> None:
    url = start.tailnet_url(Path("/x"), transport=status("Running", "fixit.tail1234.ts.net."))
    assert url == "https://fixit.tail1234.ts.net"
    assert start.tailnet_url(Path("/x"), wait_s=0, transport=status("NeedsLogin", "")) is None
    assert start.tailnet_url(Path("/no/such/socket"), wait_s=0) is None


def test_one_invite_the_first_time_only(conn: sqlite3.Connection) -> None:
    code = start.first_invite(conn)
    assert code is not None and len(code) == 11 and code[5] == "-"
    assert start.first_invite(conn) is None
    hid = store.claim_invite(conn, code, consent=True)
    assert hid is not None


def test_banner() -> None:
    b = start.banner("https://fixit.t.ts.net", "ABCDE-FGHJK")
    assert "https://fixit.t.ts.net/?invite=ABCDE-FGHJK" in b and "█" in b and "works once" in b
    assert "Already set up" in start.banner("https://fixit.t.ts.net", None)
    no_url = start.banner(None, "ABCDE-FGHJK")
    assert "FIXIT_URL" in no_url and "ABCDE-FGHJK" in no_url and "█" not in no_url
