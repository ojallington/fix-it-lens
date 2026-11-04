"""`make pilot`: the pilot's aggregate number. Counts only: never a household id, an appliance alias or a
model, and a brand is named only when at least two households have it (the rest count as "other").

    python -m eval.pilot [--db PATH] [--since ISO] [--until ISO] [--claims PREFIX]

The cohort and our own two homes (households.is_own) are reported on separate lines. A household counts once it has
consented and is not deleted; a test household (households.is_test, made by `invites new --test`) never counts, nor
does a home opened from a judges' link (invite_link_id) or through the open door (joined_via 'door'): neither signed the
pilot form, so only households that joined by invite are the cohort. A store from before those columns has invites
only.
Events count inside [since, until). The frozen reading is
`--until 2026-11-02T12:00:00+01:00 --claims pilot.r1`; the second reading (9 Nov) is `--claims pilot.r2`.
Printed whatever the value is, including 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.stats import median

PILOT_START = "2026-10-30T00:00:00+01:00"
MIN_HOMES_PER_BRAND = 2


@dataclass(frozen=True)
class Tally:
    homes: int
    appliances: int
    reminders_sent: int
    reminders_acted_on: int
    jobs_done_checked: int
    median_hours_to_done: float | None
    brands: dict[str, int]  # brand (or "other") -> homes


def default_db() -> Path:
    env = os.environ.get("FIXIT_DB")
    if env:
        return Path(env)
    priv = os.environ.get("FIXIT_PRIVATE")
    return (Path(priv) if priv else Path.home() / "Projects/hackathons/fix-it-lens-private") / "store.sqlite"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _invited_only(conn: sqlite3.Connection) -> str:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(households)")}
    return "".join(c for col, c in (("invite_link_id", " AND invite_link_id IS NULL"),
                                    ("joined_via", " AND joined_via = 'invite'")) if col in cols)


def cohort(conn: sqlite3.Connection, *, own: bool) -> list[str]:
    """The households that count: consented, not deleted, not a test, joined by invite (own homes apart). The pilot
    homes are the store's pilot_cohort view, the one definition the app's feedback route uses too; our own homes are
    not in it, so they keep this query, as does a store from before the view."""
    if not own and conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'pilot_cohort'").fetchone():
        return [r[0] for r in conn.execute("SELECT id FROM pilot_cohort")]
    return [r[0] for r in conn.execute(
        "SELECT id FROM households WHERE is_own = ? AND is_test = 0 AND consent_at IS NOT NULL "
        "AND deleted_at IS NULL" + _invited_only(conn), (int(own),))]


def tally(conn: sqlite3.Connection, *, own: bool, since: str, until: str) -> Tally:
    homes = cohort(conn, own=own)
    if not homes:
        return Tally(0, 0, 0, 0, 0, None, {})
    q = ",".join("?" * len(homes))  # placeholders only; values are bound
    apps = conn.execute(f"SELECT household_id, brand FROM appliances WHERE household_id IN ({q})", homes).fetchall()
    lo, hi = _ts(since), _ts(until)
    rows = conn.execute(f"SELECT appliance_id, job_id, kind, at, reminder_key FROM pilot_events "
                        f"WHERE household_id IN ({q})", homes).fetchall()
    events = sorted(((a, j, k, _ts(at), key) for a, j, k, at, key in rows if lo <= _ts(at) < hi),
                    key=lambda e: e[3])
    # One reminder per reminder_key whatever the channels; a send with no key counts alone.
    first_send: dict[str, tuple[Any, Any, datetime]] = {}
    for a, j, k, at, key in events:
        if k == "reminder_sent":
            first_send.setdefault(key or f"{a}:{j}:{at.isoformat()}", (a, j, at))
    acted = 0
    for key, (a, j, at) in first_send.items():
        if any((k == "reminder_opened" and rk == key) or
               (k in ("job_started", "job_done_checked") and (ea, ej) == (a, j) and t > at)
               for ea, ej, k, t, rk in events):
            acted += 1
    # Median: the first reminder_sent per (appliance, job) to the next job_done_checked on it.
    hours = []
    pending: dict[tuple[Any, Any], datetime] = {}
    done = 0
    for a, j, k, at, _ in events:
        if k == "reminder_sent":
            pending.setdefault((a, j), at)
        elif k == "job_done_checked":
            done += 1
            sent_at = pending.pop((a, j), None)
            if sent_at is not None:
                hours.append((at - sent_at).total_seconds() / 3600)
    per_brand = Counter(b for _, b in {(h, b.strip().lower()) for h, b in apps})
    brands: Counter[str] = Counter()
    for b, n in per_brand.items():
        brands[b if n >= MIN_HOMES_PER_BRAND else "other"] += n
    return Tally(len(homes), len(apps), len(first_send), acted, done,
                 round(median(hours), 1) if hours else None, dict(sorted(brands.items())))


def claims(prefix: str, t: Tally, until: str) -> list[dict[str, Any]]:
    unit_note = f"events before {until}"
    rows: list[tuple[str, Any, str]] = [
        ("homes", t.homes, "households"), ("appliances", t.appliances, "appliances"),
        ("reminders_sent", t.reminders_sent, "reminders"), ("reminders_acted_on", t.reminders_acted_on, "reminders"),
        ("jobs_done_checked", t.jobs_done_checked, "jobs done with a camera-checked pass"),
        ("median_hours_to_done", t.median_hours_to_done, "hours from reminder to done"),
        ("brands", len([b for b in t.brands if b != "other"]) + (1 if "other" in t.brands else 0), "brands"),
    ]
    return [{"id": f"{prefix}.{k}", "value": v, "unit": u, "n": t.homes, "ci": None, "note": unit_note}
            for k, v, u in rows]


def render(name: str, t: Tally) -> str:
    med = "—" if t.median_hours_to_done is None else f"{t.median_hours_to_done} h"
    brands = ", ".join(f"{b} ({n})" for b, n in t.brands.items()) or "—"
    return (f"{name}: {t.homes} homes, {t.appliances} appliances, {t.reminders_sent} reminders sent, "
            f"{t.reminders_acted_on} acted on, {t.jobs_done_checked} jobs done with a camera-checked pass, "
            f"median reminder to done {med}; brands: {brands}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.pilot")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--since", default=PILOT_START)
    ap.add_argument("--until", default=None)
    ap.add_argument("--claims", default=None, help="print claims.json lines with this id prefix")
    a = ap.parse_args(argv)
    db = a.db or default_db()
    if not db.exists():
        print(f"make pilot: no store at {db} (set FIXIT_DB or FIXIT_PRIVATE)")
        return 1
    until = a.until or datetime.now().astimezone().isoformat(timespec="seconds")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cohort = tally(conn, own=False, since=a.since, until=until)
    ours = tally(conn, own=True, since=a.since, until=until)
    if a.claims:
        for c in claims(a.claims, cohort, until) + claims(f"{a.claims}.own", ours, until):
            print(json.dumps(c, ensure_ascii=False))
        return 0
    print(f"Pilot, {a.since} to {until}")
    print(render("Cohort", cohort))
    print(render("Our own two homes", ours))
    return 0


if __name__ == "__main__":
    sys.exit(main())
