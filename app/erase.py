"""Deleting a household (DELETE /household): every file of that household goes in the same operation as its rows.

What belongs to a household, all under FIXIT_PRIVATE:
- its session recordings, sessions/<id>/ (frames, audio, events): the ids come from the store's sessions rows, from
  each recording's meta.json, and from the ledger lines of that household;
- its plate photos and settled plate reads, plates/homes/<household>/, and an older flat plates/<photo_id>.* that one
  of its recordings sent;
- its display photos, displays/homes/<household>/;
- its own cards (with their check records) and manuals (uploads, fetched copies, page caches), cards/homes/<household>/
  and manuals/homes/<household>/;
- the request bodies and response fixtures of its calls, requests/<sha>.json and fixtures*/<lane>/<sha>.jsonl, unless
  a call of another household, or one with no household, sent the same body;
- its feedback answers, by app/feeds/feedback.py (under the lock its writes take, which refuse a home being deleted);
- its ledger lines keep their cost but lose the household and session ids;
- the card_library rows of its own cards (they name the home and its model), with its rows, in one transaction.

Before any file goes, the household is marked as being deleted (a new WS hello with its token is closed with
CLOSE_CODE), and whatever runs for it is ended on its event loop: each open WS session is closed with CLOSE_CODE and
stopped, each running ingest cancelled, so nothing writes a file after the sweep. Files go next and the rows last, so a
failure leaves the household in place to delete again. The log names counts per kind and never a path's contents."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
import weakref
from pathlib import Path
from typing import Any, Protocol

import calls
from app import config, recorder
from app.feeds import feedback
from app.schedule import store
from contracts.models.ws import GoneClose

log = logging.getLogger(__name__)

CLOSE_CODE = 4410  # ws GoneClose: final, the phone does not reconnect
CLOSE_REASON = GoneClose(error="household_deleted").model_dump_json()
END_TIMEOUT_S = 10.0


class Live(Protocol):
    """What runs for households on an event loop (the WS sessions, the ingests): ended before the files go."""

    loop: asyncio.AbstractEventLoop | None

    async def end_household(self, household_id: str) -> dict[str, int]:
        """Stop everything of this household; the count per kind of what was stopped."""
        ...


_live: list[weakref.ref[Any]] = []
_deleting: set[str] = set()


def watch(live: Live) -> None:
    """Have a delete end this holder's work for the household first (held weakly: a dropped app is forgotten)."""
    _live[:] = [r for r in _live if r() is not None]
    _live.append(weakref.ref(live))


def deleting(household_id: str | None) -> bool:
    """True from the start of this household's delete on: its WS hello is refused."""
    return household_id is not None and household_id in _deleting


def _end_live(household_id: str) -> dict[str, int]:
    """End the household's live work on each holder's own loop, and wait for it. Called off the loop (the delete
    route is a plain def, run in the threadpool)."""
    counts: dict[str, int] = {}
    for ref in list(_live):
        live = ref()
        loop = getattr(live, "loop", None)
        if live is None or loop is None or loop.is_closed() or not loop.is_running():
            continue
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            raise RuntimeError("erase.household blocks on the event loop's work; call it off the loop")
        done = asyncio.run_coroutine_threadsafe(live.end_household(household_id), loop).result(END_TIMEOUT_S)
        for k, v in done.items():
            counts[k] = counts.get(k, 0) + v
    return counts


def _ledger_lines(priv: Path) -> list[dict[str, Any]]:
    f = priv / "ledger.jsonl"
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _session_ids(conn: sqlite3.Connection, priv: Path, household_id: str, lines: list[dict[str, Any]]) -> set[str]:
    ids = {r[0] for r in conn.execute("SELECT id FROM sessions WHERE household_id = ?", (household_id,))}
    ids |= {str(r["session_id"]) for r in lines if r.get("household") == household_id and r.get("session_id")}
    root = priv / "sessions"
    for meta in root.glob("*/meta.json") if root.is_dir() else []:
        try:
            if json.loads(meta.read_text(encoding="utf-8")).get("household") == household_id:
                ids.add(meta.parent.name)
        except ValueError:
            continue
    return ids


def _plate_ids(session_dirs: list[Path]) -> set[str]:
    """The photo ids of the plate photos these recordings sent."""
    ids = set()
    for d in session_dirs:
        f = d / "events.jsonl"
        for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            msg = ev.get("msg") if isinstance(ev, dict) else None
            if ev.get("dir") == "c2s" and ev.get("kind") == "plate_photo" and isinstance(msg, dict) \
                    and msg.get("photo_id"):
                ids.add(str(msg["photo_id"]))
    return ids


def _request_shas(lines: list[dict[str, Any]], household_id: str, sessions: set[str]) -> set[str]:
    """The request hashes only this household's calls sent. A body is keyed by its bytes alone, so another home or
    a run with no home (a test ingest of the same PDF) may share it: then every line with that hash must be ours."""
    def ours(r: dict[str, Any]) -> bool:
        return r.get("household") == household_id or (r.get("session_id") in sessions and not r.get("household"))

    mine = {str(r["request_sha256"]) for r in lines if r.get("request_sha256") and ours(r)}
    others = {str(r["request_sha256"]) for r in lines if r.get("request_sha256") and not ours(r)}
    return mine - others


def files(conn: sqlite3.Connection, household_id: str) -> dict[str, list[Path]]:
    """Every file and folder of this household, by kind (nothing is deleted here)."""
    priv = config.private_dir()
    if priv is None:
        return {}
    lines = _ledger_lines(priv)
    sids = _session_ids(conn, priv, household_id, lines)
    sessions = [priv / "sessions" / recorder.safe_id(s) for s in sorted(sids)]
    sessions = [d for d in sessions if d.is_dir()]
    flat = priv / "plates"
    plates = [p for pid in sorted(_plate_ids(sessions))
              for p in (flat / f"{recorder.safe_id(pid)}.jpg", flat / f"{recorder.safe_id(pid)}.json")]
    shas = _request_shas(lines, household_id, sids)
    return {
        "sessions": sessions,
        "plates": [flat / "homes" / household_id, *plates],
        "cards": [priv / "cards" / "homes" / household_id],
        "manuals": [priv / "manuals" / "homes" / household_id],
        "displays": [priv / "displays" / "homes" / household_id],
        "requests": [priv / "requests" / f"{sha}.json" for sha in sorted(shas)],
        "fixtures": [p for sha in sorted(shas) for p in priv.glob(f"fixtures*/*/{sha}.jsonl")],
    }


def _own_card_rows(conn: sqlite3.Connection, household_id: str) -> list[str]:
    """The card_library rows of this household's own cards (its files under cards/homes/<household>/, archived
    versions too): they name the home in their path, and its brand and model. A card it built that was shared stays,
    with built_by gone by the cascade."""
    def in_home(path: str) -> bool:
        parts = Path(path).parts
        return any(parts[i:i + 3] == ("cards", "homes", household_id) for i in range(len(parts) - 2))

    rows = conn.execute("SELECT card_path, built_by, shared FROM card_library").fetchall()
    return [r["card_path"] for r in rows
            if r["built_by"] == household_id and not r["shared"] or in_home(r["card_path"])]


def _delete_rows(conn: sqlite3.Connection, household_id: str) -> int:
    """The household's rows (by cascade) and its own cards' card_library rows, in one transaction. Returns the
    card_library rows removed."""
    own = _own_card_rows(conn, household_id)
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN IMMEDIATE")
    try:
        store.delete_household(conn, household_id)  # its reports and unlinks of those cards go with it
        conn.executemany("DELETE FROM card_library WHERE card_path = ?", [(p,) for p in own])
    except BaseException:
        if began:
            conn.execute("ROLLBACK")
        raise
    if began:
        conn.execute("COMMIT")
    return len(own)


def _remove(p: Path) -> int:
    """The number of files removed with this path (a folder counts its files)."""
    if p.is_dir() and not p.is_symlink():
        n = sum(1 for f in p.rglob("*") if not f.is_dir())
        shutil.rmtree(p)
        return n
    if p.exists() or p.is_symlink():
        p.unlink()
        return 1
    return 0


def household(conn: sqlite3.Connection, household_id: str) -> dict[str, int]:
    """Delete the household: end its open sessions and ingests, then its files, then its ledger ids, then its rows (by
    cascade). Returns what was ended and the files removed per kind, plus the ledger lines that lost its ids."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", household_id):  # it names folders below
        raise ValueError("a household id is letters, digits, _ and - only")
    _deleting.add(household_id)  # kept after the delete too: the id never comes back
    try:
        ended = {"sessions_open": 0, "ingests": 0} | _end_live(household_id)
        counts = ended | {kind: sum(_remove(p) for p in paths) for kind, paths in files(conn, household_id).items()}
        priv = config.private_dir()
        # no folder, no answers: a write since would make it before asking deleting(), so is refused
        counts["feedback_lines"] = feedback.erase(priv, household_id) \
            if priv is not None and (priv / feedback.DIR).is_dir() else 0
        counts["ledger_lines"] = calls.Ledger(priv / "ledger.jsonl", priv / "budgets.toml").forget_household(
            household_id) if priv is not None and (priv / "ledger.jsonl").exists() else 0
        counts["card_rows"] = _delete_rows(conn, household_id)
    except BaseException:
        _deleting.discard(household_id)  # the household stays, and may be deleted again
        raise
    log.info("household deleted: %s", ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts
