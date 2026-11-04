"""The demo home: a test household with real cards and a lived-in schedule, for showing one due job everywhere.

    uv run python -m deploy.demo_home --admin <first name> --dry-run   # what it would do; writes nothing
    uv run python -m deploy.demo_home --admin <first name>             # make it, or finish making it

It is a test household (`invites new --test`): never an own home, never in a pilot count, sent no reminders. Its
appliances are added by model code through the app's own add path, from cards already built, so no model is called.
An appliance is added only once a person has read its card and marked it usable
(FIXIT_PRIVATE/first_time/verdicts.jsonl: the latest line for that card, and only while the card's bytes are the ones
that were read). One with no usable card yet is left out and named; a later run adds it once it is. Every date
goes in through the schedule's own writes (manage.py), as the household's first admin: done dates in the past, one
job due in a few days, one overdue, one inside a window, one snoozed and one done recently, counted back from the day
it is added, and one job whose manual gives no interval on the household's own (the admin's choice, written as theirs:
no model proposes it). The run names any of those six the chosen cards cannot show.

Its Home Assistant token, agent token and calendar address go to 0600 files in FIXIT_PRIVATE/demo/, and are never
printed. A second run finds the home by its name and changes nothing it already made. The store is FIXIT_DB (or
FIXIT_PRIVATE/store.sqlite), as for invites.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from app import config
from app.schedule import manage, store
from app.schedule.dates import household_tz, local_today
from app.schedule.due import CardLoader, Schedule, due
from app.schedule.invites import db_path

NAME = "Demo home"
COUNTRY, LANG = "nl", "en"

# The appliances of the home it shows, by model code: (alias, brand, model code)
HOME = (
    ("dishwasher", "bosch", "SMV4HAX40N"),
    ("washer", "aeg", "LR7696UD4"),
    ("boiler", "intergas", "Xtreme 36"),
    ("fridge", "electrolux", "KNA6LE18S"),
    ("oven", "aeg", "OS6PB52WAB"),
)
# Cards whose first read is not enough: only a reread after their fixes counts (the fridge's first card quotes
# another language's section of its manual).
REREAD_ONLY = {"KNA6LE18S"}
Appliances = tuple[tuple[str, str, str], ...]  # (alias, brand, model code)


@dataclass(frozen=True)
class JobPlan:
    alias: str
    job_id: str
    done_days_ago: int
    window: tuple[int, int] | None = None   # months: due from the first, overdue after the second
    # the household's own interval, for a job whose manual gives none
    own_every: tuple[int, Literal["day", "week", "month", "year"]] | None = None
    done_month: int | None = None           # done on the 15th of this month, the latest one before today
    snooze_days: int | None = None          # from today
    model: str | None = None                # the card it is planned for; None: whichever the alias has
    shows: tuple[str, ...] = ()             # which of CASES it is there to show


JOBS = (
    JobPlan("dishwasher", "machine_cleaning", 75, model="SMV4HAX40N", shows=("overdue",)),      # every 2 months
    JobPlan("washer", "maintenance_wash", 27, model="LR7696UD4", shows=("due soon",)),          # monthly
    JobPlan("washer", "clean_door_seal", 70, window=(2, 4), model="LR7696UD4", shows=("window",)),
    JobPlan("washer", "descale", 190, snooze_days=7, model="LR7696UD4", shows=("snoozed",)),    # every 6 months
    JobPlan("washer", "clean_drum", 20, model="LR7696UD4", shows=("done recently",)),          # every 2 months
    JobPlan("washer", "clean_detergent_dispenser", 35, model="LR7696UD4"),
    JobPlan("washer", "clean_drain_pump_filter", 100, model="LR7696UD4"),
    JobPlan("washer", "clean_inlet_hose_and_valve_filter", 150, model="LR7696UD4"),
    # the admin's own choice: bleed the radiators every year in September, when the heating comes back on
    JobPlan("boiler", "bleed_radiators", 0, own_every=(1, "year"), done_month=9, model="Xtreme 36",
            shows=("own interval",)),
)
TOKENS = (("ha", "write", "Home Assistant", "ha.token"), ("agent", "write", "my agent", "agent.token"))
CASES = ("overdue", "due soon", "window", "snoozed", "done recently", "own interval")
VERDICTS = "first_time/verdicts.jsonl"


def usable_cards(priv: Path) -> dict[str, str]:
    """The card paths whose latest verdict is usable and whose file is still the one that was read, each with the
    read it came from (baseline, rerun or curated)."""
    latest: dict[str, dict[str, object]] = {}
    path = priv / VERDICTS
    for line in path.read_text().splitlines() if path.exists() else []:
        if line.strip():
            v = json.loads(line)
            latest[str(v["card"])] = v  # in file order: the last line for a card is its latest
    ok: dict[str, str] = {}
    for rel, v in latest.items():
        # repo and private cards both sit under cards/: the path is either one's, and the bytes say which was read
        files = [f for f in (config.ROOT / rel, priv / rel) if f.is_file()]
        if v.get("verdict") == "usable" and any(
                hashlib.sha256(f.read_bytes()).hexdigest() == v.get("card_sha256") for f in files):
            ok[rel] = str(v.get("run"))
    return ok


def card_rel(brand: str, model: str, priv: Path) -> str | None:
    """A built card's path as the verdicts name it: repo-relative for a repo card, else FIXIT_PRIVATE-relative."""
    from ingest.flow import find_card

    found = find_card(brand, model)
    if found is None:
        return None
    p = found.resolve()
    for root in (config.ROOT.resolve(), priv.resolve()):
        if root in p.parents:
            return str(p.relative_to(root))
    return None


def plan(priv: Path) -> tuple[Appliances, tuple[JobPlan, ...], list[str]]:
    """The appliances whose cards are read usable, their jobs, and what is left out or missing, in words."""
    ok = usable_cards(priv)
    chosen: list[tuple[str, str, str]] = []
    notes: list[str] = []
    for alias, brand, model in HOME:
        run = ok.get(card_rel(brand, model, priv) or "")
        if run is None:
            notes.append(f"left out the {alias}: its card ({brand} {model}) is not read usable yet")
        elif model in REREAD_ONLY and run != "rerun":
            notes.append(f"left out the {alias}: its card ({brand} {model}) waits for its reread")
        else:
            chosen.append((alias, brand, model))
    models = {a: m for a, _, m in chosen}
    jobs = tuple(j for j in JOBS if j.alias in models and j.model in (None, models[j.alias]))
    shown = {c for j in jobs for c in j.shows}
    notes += [f"no {c} job: the chosen cards cannot show one" for c in CASES if c not in shown]
    return tuple(chosen), jobs, notes


def private_dir() -> Path:
    p = os.environ.get("FIXIT_PRIVATE")
    if not p:
        raise SystemExit("FIXIT_PRIVATE is not set: the demo home's cards and tokens live there")
    return Path(p)


def find_home(conn: sqlite3.Connection) -> str | None:
    if "name" not in {r[1] for r in conn.execute("PRAGMA table_info(households)")}:
        return None  # a store from before households had names: no demo home yet
    row = conn.execute("SELECT id FROM households WHERE name = ? AND is_test = 1 AND deleted_at IS NULL",
                       (NAME,)).fetchone()
    return str(row["id"]) if row else None


def missing_cards(appliances: Appliances) -> list[str]:
    from ingest.flow import find_card

    return [f"{brand} {model}" for _, brand, model in appliances if find_card(brand, model) is None]


def write_secret(path: Path, text: str) -> None:
    """A new 0600 file; the text is never echoed."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text + "\n")


def _last_in(month: int, today: date) -> date:
    """The 15th of `month`, the latest one on or before today."""
    d = date(today.year, month, 15)
    return d if d <= today else date(today.year - 1, month, 15)


def _no_interval(conn: sqlite3.Connection, appliance_id: str, job_id: str, load_card: CardLoader) -> bool:
    """The job is upkeep on the appliance's checked card and its manual gives no interval."""
    row = conn.execute("SELECT card_path FROM cards_ref WHERE appliance_id = ?", (appliance_id,)).fetchone()
    card = load_card(row["card_path"]) if row else None
    job = next((x for x in card.jobs if x.id == job_id), None) if card else None
    return job is not None and job.kind == "upkeep" and job.interval is None


def _appliance_ids(conn: sqlite3.Connection, hid: str) -> dict[str, str]:
    return {r["alias"]: r["id"] for r in conn.execute("SELECT id, alias FROM appliances WHERE household_id = ?",
                                                     (hid,))}


def seed(conn: sqlite3.Connection, secret: bytes, out: Path, *, now: datetime, dry_run: bool,
         load_card: CardLoader, admin: str, say: Callable[[str], object] = print,
         appliances: Appliances, jobs: tuple[JobPlan, ...]) -> list[str]:
    """Make the demo home, or the parts of it still missing; returns what was (or would be) done."""
    from app.adding import Adder
    from app.store import Store

    done: list[str] = []

    def step(line: str) -> None:
        done.append(line)
        say(("would: " if dry_run else "") + line)

    missing = missing_cards(appliances)
    if missing:
        raise SystemExit(f"no card for {', '.join(missing)}: the demo home never builds one")
    hid = find_home(conn)
    if hid is None:
        step(f"make a test household named {NAME!r} with its first admin {admin!r}")
        if dry_run:
            hid = "(new)"
        else:
            with conn:
                inv = store.create_invites(conn, 1, is_test=True, country=COUNTRY, lang=LANG, at=now)[0]
                hid = store.claim_invite(conn, inv.code, consent=True, at=now)
                assert hid is not None
                conn.execute("UPDATE households SET name = ? WHERE id = ?", (NAME, hid))
                conn.execute("UPDATE people SET name = ? WHERE household_id = ? AND role = 'admin'", (admin, hid))
    have = _appliance_ids(conn, hid) if not dry_run or hid != "(new)" else {}
    todo = [(a, b, m) for a, b, m in appliances if a not in have]
    for alias, _, model in todo:
        step(f"add the {alias} by its model code {model}")
    if todo and not dry_run:
        hh = dict(conn.execute("SELECT * FROM households WHERE id = ?", (hid,)).fetchone())
        adder = Adder(Store(conn, secret))

        async def add_all() -> None:
            for alias, brand, model in todo:
                adder.start(hh, {"alias": alias, "kind": alias, "brand": brand, "model": model}, None)
            await adder.idle()
        asyncio.run(add_all())
        bad = [r["alias"] for r in conn.execute(
            "SELECT a.alias FROM appliances a LEFT JOIN cards_ref c ON c.appliance_id = a.id"
            " WHERE a.household_id = ? AND COALESCE(c.status, 'pending') != 'checked'", (hid,))]
        if bad:
            raise SystemExit(f"these did not get their checked card: {', '.join(bad)}")
    ids = _appliance_ids(conn, hid) if hid != "(new)" else {}
    caller = store.caller_from_token(conn, secret, store.household_token(secret, hid)) if ids else None
    if ids:
        dated = {(d.appliance_id, d.job_id) for d in due(conn, hid, now, load_card)}
        off = [f"{j.alias} {j.job_id}" for j in jobs if j.alias in ids and not j.own_every
               and (ids[j.alias], j.job_id) not in dated]
        off += [f"{j.alias} {j.job_id} (the manual gives it an interval, or it is not upkeep)" for j in jobs
                if j.alias in ids and j.own_every and not _no_interval(conn, ids[j.alias], j.job_id, load_card)]
        if off:
            raise SystemExit(f"not on the schedule, so no date was written: {', '.join(off)}")
    today = local_today(now, household_tz(COUNTRY))
    for j in jobs:
        aid = ids.get(j.alias)
        if aid and conn.execute("SELECT 1 FROM job_change WHERE household_id = ? AND appliance_id = ? AND job_id = ?",
                                (hid, aid, j.job_id)).fetchone():
            continue  # already made: a second run changes nothing
        on = _last_in(j.done_month, today) if j.done_month else today - timedelta(days=j.done_days_ago)
        step(f"{j.alias} {j.job_id}: done {on.isoformat()}"
             + (f", your window {j.window[0]} to {j.window[1]} months" if j.window else "")
             + (f", {admin}'s own interval: every {j.own_every[0]} {j.own_every[1]} (the manual gives none)"
                if j.own_every else "")
             + (f", snoozed to {(today + timedelta(days=j.snooze_days)).isoformat()}" if j.snooze_days else ""))
        if dry_run or aid is None or caller is None:
            continue
        with conn:
            if j.own_every:  # first: such a job is on the schedule only once it has the household's own interval
                manage.set_schedule(conn, hid, aid, j.job_id, by=caller, at=now, schedule=Schedule(
                    kind="every", every=j.own_every[0], unit=j.own_every[1]))
            manage.mark_done(conn, hid, aid, j.job_id, on=on, today=today, by=caller, at=now)
            if j.window:
                manage.set_schedule(conn, hid, aid, j.job_id, by=caller, at=now, schedule=Schedule(
                    kind="window", min=j.window[0], max=j.window[1], unit="month"))
            if j.snooze_days:
                due_on = next(d.due_on for d in due(conn, hid, now, load_card)
                              if d.appliance_id == aid and d.job_id == j.job_id)
                manage.snooze(conn, hid, aid, j.job_id, until=today + timedelta(days=j.snooze_days), due_on=due_on,
                              today=today, by=caller, at=now)
    for kind, scope, label, file in TOKENS:
        if not (out / file).exists():
            step(f"issue its {label} token ({scope}) to {out / file} (0600, not shown)")
            if not dry_run:
                _, tok = store.issue_token(conn, hid, by_kind=kind, scope=scope, label=label,  # type: ignore[arg-type]
                                           at=now)
                conn.commit()
                write_secret(out / file, tok)
    if not (out / "feed.path").exists():
        step(f"write its calendar address to {out / 'feed.path'} (0600, not shown)")
        if not dry_run:
            write_secret(out / "feed.path", f"/api/v1/feeds/{store.feed_token(secret, hid)}.ics")
    if not done:
        say(f"{NAME} is complete; nothing to do")
    return done


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="demo_home", description=__doc__.split("\n")[0])
    ap.add_argument("--admin", required=True, help="the first admin's first name, as the app shows it")
    ap.add_argument("--dry-run", action="store_true", help="say what it would do, write nothing")
    args = ap.parse_args(argv)
    from app.server import checked_card_loader

    priv = private_dir()
    path = db_path()
    secret_path = path.parent / "secrets/household.secret"
    if not path.exists() or not secret_path.exists():
        raise SystemExit(f"no store at {path}: start the server once first")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # a dry run writes nothing, not even a migration
    conn.row_factory = sqlite3.Row
    version, want = int(conn.execute("PRAGMA user_version").fetchone()[0]), max(n for n, _ in store._migrations())
    if not args.dry_run:
        if version < want:  # the server that is running must migrate its own store, not this script under it
            raise SystemExit(f"the store is at version {version} and this build wants {want}: restart the server on "
                             "this build first")
        conn = store.open_store(path)
    appliances, jobs, notes = plan(priv)
    for n in notes:
        print(n)
    if not appliances:
        raise SystemExit(f"no card is read usable yet ({priv / VERDICTS}): nothing to make")
    seed(conn, store.load_secret(secret_path), priv / "demo", now=datetime.now(UTC), dry_run=args.dry_run,
         load_card=checked_card_loader(COUNTRY), admin=args.admin, appliances=appliances, jobs=jobs)


if __name__ == "__main__":
    main()
