"""/preflight: opened 10 minutes before a live run; every line green or red.

Server lines: the models answer with the pinned IDs, the ledger has headroom, the manuals are in place, local mode is
loaded, the certificate has more than 7 days, the last replay recording reads, Home Assistant answers. The phone's
own lines (rtt, battery) are added by deploy/preflight.html in the browser. A check that cannot run is red, never
skipped; each check has its own timeout and never raises.
"""

from __future__ import annotations

import asyncio
import os
import socket
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from calls.ledger import Ledger

CHECK_TIMEOUT_S = 5.0
MIN_HEADROOM_USD = 5.0
MIN_CERT_DAYS = 7


@dataclass(frozen=True)
class Line:
    check: str
    ok: bool
    detail: str

    def json(self) -> dict[str, Any]:
        return {"check": self.check, "ok": self.ok, "detail": self.detail}


Check = Callable[[], Awaitable[Line]]


def ledger_check(ledger: Ledger | None, lanes: tuple[str, ...] = ("l1", "pilot", "reserve")) -> Check:
    async def run() -> Line:
        if ledger is None:
            return Line("ledger", False, "no ledger (FIXIT_PRIVATE unset)")
        cap, budgets = ledger.budgets()
        t = ledger.totals()
        room = cap - t.total
        tight = [f"{ln} ${budgets[ln] - t.by_lane.get(ln, 0.0):.2f}" for ln in lanes if ln in budgets
                 and budgets[ln] - t.by_lane.get(ln, 0.0) < 1.0]
        ok = room >= MIN_HEADROOM_USD and not tight
        return Line("ledger", ok, f"${room:.2f} left of ${cap:.0f}" + (f"; low: {', '.join(tight)}" if tight else ""))
    return run


def calls_check(ledger: Ledger | None, mode: str | None = None) -> Check:
    """Which mode calls/ runs in (FIXIT_CALLS): replay spends nothing; record and live go to the Meta API through
    the ledger, so they need it."""
    async def run() -> Line:
        m = mode or os.environ.get("FIXIT_CALLS", "replay")
        if m == "replay":
            return Line("calls", True, "replay (recorded fixtures, no key, nothing spent)")
        if m not in ("record", "live"):
            return Line("calls", False, f"FIXIT_CALLS={m!r}: not live, record or replay")
        if ledger is None:
            return Line("calls", False, f"{m} (live) with no ledger: every call is refused")
        cap, _ = ledger.budgets()
        return Line("calls", True, f"{m} (live, ledger ${ledger.totals().total:.2f} of ${cap:.0f})")
    return run


def cert_check(host: str, port: int = 443) -> Check:
    def read() -> datetime:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=CHECK_TIMEOUT_S) as s, \
                ctx.wrap_socket(s, server_hostname=host) as tls:
            cert = tls.getpeercert()
        assert cert is not None
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(str(cert["notAfter"])), UTC)

    async def run() -> Line:
        name = f"certificate {host}:{port}"
        try:
            until = await asyncio.to_thread(read)
        except (OSError, ssl.SSLError, AssertionError) as e:
            return Line(name, False, f"no valid certificate: {e}")
        days = (until - datetime.now(UTC)).days
        return Line(name, days > MIN_CERT_DAYS, f"valid {days} more days (to {until.date()})")
    return run


async def no_cert() -> Line:
    return Line("certificate", False, "not opened over HTTPS: open /api/v1/preflight on the phone's address")


def manuals_check(cards_dir: Path, pages: Callable[[str, str], list[Any]]) -> Check:
    """Every card in cards/ has its manual's pages in the private folder (the pages the citation check reads)."""
    async def run() -> Line:
        from app.cards import read

        cards = sorted(cards_dir.glob("*/*.yaml"))
        if not cards:
            return Line("manuals", False, "no cards")
        missing = []
        for path in cards:
            card = read(path)
            if not pages(card.brand, card.model):
                missing.append(f"{card.brand} {card.model}")
        n = len(cards) - len(missing)
        return Line("manuals", not missing,
                    f"{n} of {len(cards)} manuals in place" + (f"; missing: {', '.join(missing)}" if missing else ""))
    return run


def replay_check(sessions_dir: Path | None) -> Check:
    """The newest recording loads as ?replay= would load it, and has events."""
    async def run() -> Line:
        from app import recorder

        if sessions_dir is None or not sessions_dir.is_dir():
            return Line("last replay", False, "no sessions folder")
        runs = sorted(sessions_dir.glob("*/meta.json"), key=lambda p: p.stat().st_mtime)
        if not runs:
            return Line("last replay", False, "no recording")
        sid = runs[-1].parent.name
        try:
            n = len(recorder.load(sessions_dir, sid).lines)
        except (OSError, ValueError) as e:
            return Line("last replay", False, f"{sid}: unreadable ({type(e).__name__})")
        return Line("last replay", n > 0, f"{sid}: {n} events")
    return run


def probe_check(name: str, probe: Callable[[], Awaitable[dict[str, Any]]] | None) -> Check:
    """A probe from calls/ (models, local mode): only calls/ may reach a model endpoint."""
    async def run() -> Line:
        if probe is None:
            return Line(name, False, "no probe yet")
        r = await probe()
        return Line(name, bool(r.get("ok")), str(r.get("detail", "")))
    return run


def ha_check(available: Callable[[], Awaitable[bool]] | None) -> Check:
    async def run() -> Line:
        if available is None:
            return Line("home assistant", True, "not set up (optional)")
        ok = await available()
        return Line("home assistant", ok, "answers (read-only)" if ok else "no answer")
    return run


async def run_checks(checks: list[tuple[str, Check]]) -> list[Line]:
    async def one(name: str, c: Check) -> Line:
        try:
            return await asyncio.wait_for(c(), CHECK_TIMEOUT_S + 1)
        except TimeoutError:
            return Line(name, False, "timed out")
        except Exception as e:  # a broken check is a red line, not a 500
            return Line(name, False, f"check failed: {type(e).__name__}: {e}")

    return list(await asyncio.gather(*(one(n, c) for n, c in checks)))


def fixed(line: Line) -> Check:
    async def run() -> Line:
        return line
    return run


def default_checks(cert_hosts: list[tuple[str, int]], *, ledger: Ledger | None, private: Path | None,
                   cards_dir: Path, ha_available: Callable[[], Awaitable[bool]] | None = None,
                   mode: str | None = None) -> list[tuple[str, Check]]:
    """In replay mode (a fresh install, no key) the key, the local model, the free credits and the ledger are not
    used, so their lines say so."""
    import calls
    from app.ha.client import configured
    from app.ports import IngestManuals

    replay = (mode or os.environ.get("FIXIT_CALLS", "replay")) == "replay"
    checks: list[tuple[str, Check]] = [
        ("models", fixed(Line("models", True, "replay: not called, no key needed")) if replay
         else probe_check("models", calls.preflight_models)),
        ("local", fixed(Line("local", True, "replay: not used")) if replay
         else probe_check("local", calls.preflight_local)),
        ("credits", fixed(Line("credits", True, "replay: not used")) if replay
         else probe_check("credits", calls.preflight_credits)),
        ("calls", calls_check(ledger, mode)),
        ("ledger", fixed(Line("ledger", True, "replay: nothing spent")) if replay else ledger_check(ledger)),
        ("manuals", manuals_check(cards_dir, IngestManuals().manual_pages)),
        ("last replay", replay_check(private / "sessions" if private else None)),
        ("home assistant", ha_check(ha_available if configured() else None)),  # no token: not set up (optional)
    ]
    checks += [(f"certificate {h}:{p}", cert_check(h, p)) for h, p in cert_hosts]
    return checks
