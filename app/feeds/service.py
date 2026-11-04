"""Wiring for app/server.py: build the feeds' Deps from paths, and the push timer.

    deps = feeds_deps(conn, load_card)
    app.include_router(make_router(deps), prefix="/api/v1")
    @app.on_event("startup")  # or the lifespan
    async def _start() -> None: asyncio.create_task(push_loop(deps))

/api/v1/preflight runs the checks of app/feeds/preflight.py (pass ha_available=HA(...).available for the HA
line). Secrets are read by path: the token secret at $FIXIT_PRIVATE/secrets/household.secret (made on first run),
the VAPID key at ~/.config/fleet/fixit-vapid.pem (FIXIT_VAPID overrides the path). With no VAPID key, push is off
and the .ics feed still works.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import calls
from app.feeds import caps, preflight, push
from app.feeds.router import Deps
from app.schedule.due import CardLoader
from app.schedule.invites import db_path
from app.schedule.store import load_secret

log = logging.getLogger("fixit.feeds")
PUSH_EVERY_S = 300


def private_root() -> Path:
    return db_path().parent


ROOT = Path(__file__).resolve().parents[2]


def feeds_deps(conn: sqlite3.Connection, load_card: CardLoader, *,
               ha_available: Callable[[], Awaitable[bool]] | None = None) -> Deps:
    secret = load_secret(private_root() / "secrets/household.secret")
    try:
        vapid: push.Vapid | None = push.load_vapid(Path(os.environ.get("FIXIT_VAPID", push.DEFAULT_VAPID)))
    except FileNotFoundError:
        log.warning("no VAPID key: push is off; run `python -m app.feeds.push_keys` to make one")
        vapid = None
    private = calls.private_dir()
    ledger = calls.Ledger(private / "ledger.jsonl", private / "budgets.toml") if private is not None else None
    checks = functools.partial(preflight.default_checks, ledger=ledger, private=private, cards_dir=ROOT / "cards",
                               ha_available=ha_available)
    spend = functools.partial(caps.households_spend, ledger) if ledger is not None else None
    return Deps(conn, secret, load_card, vapid, preflight=checks, warm_up=calls.warm_up_local, spend=spend,
                private=private)


async def push_loop(deps: Deps, every_s: float = PUSH_EVERY_S) -> None:
    if deps.vapid is None:
        return
    send = push.webpush_sender(deps.vapid)
    while True:
        try:
            sent = await asyncio.to_thread(push.dispatch, deps.conn, datetime.now(UTC), deps.load_card, send)
            for s in sent:
                log.info("push %s: delivered %d, gone %d", s.key, s.delivered, s.gone)
        except Exception:  # a bad tick must not stop the reminders
            log.exception("push dispatch failed")
        await asyncio.sleep(every_s)
