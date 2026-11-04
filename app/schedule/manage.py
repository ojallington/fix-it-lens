"""The household's own changes to its schedule: mark done, the "last done?" answer, snooze, its own interval or
window, and undo. Every change is a job_change row with who made it (the token's kind) and the state before and
after, so the last one can be undone. The routes in app/feeds/router.py are the only callers; the phone, an agent
(MCP), Home Assistant and curl all come through them.

The card is never changed: the manual's interval stays the cited truth, and a household schedule sits beside it in
job_schedule. No model writes any of this. A snooze only ever moves a due date later.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any, Literal

from app.schedule import store
from app.schedule.due import Schedule, schedule_of
from app.schedule.store import Caller, now_iso

ChangeKind = Literal["done", "last_done", "schedule", "snooze", "assignee", "undo"]


class Conflict(Exception):
    """Nothing to undo, or the job changed since the change it would undo."""


class NotOwn(Conflict):
    """An undo asked for the caller's own change, and the latest one is someone else's: nothing was undone."""


def state(conn: sqlite3.Connection, appliance_id: str, job_id: str) -> dict[str, Any]:
    """The contract's JobState, as stored: last_done, last_done_source, schedule, snoozed_until, assignee."""
    js = conn.execute("SELECT last_done, last_done_source FROM job_state WHERE appliance_id = ? AND job_id = ?",
                      (appliance_id, job_id)).fetchone()
    row = conn.execute("SELECT * FROM job_schedule WHERE appliance_id = ? AND job_id = ?",
                       (appliance_id, job_id)).fetchone()
    sched, snoozed = schedule_of(row)
    return {"last_done": js["last_done"] if js else None, "last_done_source": js["last_done_source"] if js else None,
            "schedule": sched.api() if sched else None, "snoozed_until": snoozed.isoformat() if snoozed else None,
            "assignee": row["assignee"] if row else None}


def _put(conn: sqlite3.Connection, appliance_id: str, job_id: str, st: dict[str, Any], ts: str) -> None:
    if st["last_done_source"] is None:
        conn.execute("DELETE FROM job_state WHERE appliance_id = ? AND job_id = ?", (appliance_id, job_id))
    else:
        conn.execute(
            "INSERT INTO job_state (appliance_id, job_id, last_done, last_done_source, updated_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (appliance_id, job_id) DO UPDATE SET"
            " last_done = excluded.last_done, last_done_source = excluded.last_done_source,"
            " updated_at = excluded.updated_at",
            (appliance_id, job_id, st["last_done"], st["last_done_source"], ts))
    s = st["schedule"]
    if s is None and st["snoozed_until"] is None and st["assignee"] is None:
        conn.execute("DELETE FROM job_schedule WHERE appliance_id = ? AND job_id = ?", (appliance_id, job_id))
        return
    conn.execute(
        "INSERT INTO job_schedule (appliance_id, job_id, kind, every, min, max, unit, snoozed_until, assignee,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (appliance_id, job_id) DO UPDATE SET"
        " kind = excluded.kind, every = excluded.every, min = excluded.min, max = excluded.max, unit = excluded.unit,"
        " snoozed_until = excluded.snoozed_until, assignee = excluded.assignee, updated_at = excluded.updated_at",
        (appliance_id, job_id, s and s["kind"], s and s.get("every"), s and s.get("min"), s and s.get("max"),
         s and s["unit"], st["snoozed_until"], st["assignee"], ts))


def _log(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, kind: ChangeKind,
         by: Caller, before: dict[str, Any], after: dict[str, Any], ts: str, undoes: int | None = None) -> int:
    if by.by_kind == "feed":
        raise ValueError("a feed never writes")
    cur = conn.execute(
        "INSERT INTO job_change (household_id, appliance_id, job_id, at, kind, by_kind, by_person, by_token, by_label,"
        " before_json, after_json, undoes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (household_id, appliance_id, job_id, ts, kind, by.by_kind, by.person_id, by.token_id, by.label,
         json.dumps(before), json.dumps(after), undoes))
    return int(cur.lastrowid or 0)


def _change(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, kind: ChangeKind,
            by: Caller, at: datetime | None, **set_: Any) -> None:
    before = state(conn, appliance_id, job_id)
    after = {**before, **set_}
    if after == before:
        return  # nothing changed, nothing to log
    ts = now_iso(at)
    with conn:
        _put(conn, appliance_id, job_id, after, ts)
        if kind == "done":
            hid = conn.execute(
                "INSERT INTO history (household_id, appliance_id, kind, job_id, text, on_date, created_at)"
                " VALUES (?, ?, 'job_done', ?, ?, ?, ?)",
                (household_id, appliance_id, job_id, f"marked done ({by.label})", after["last_done"], ts)).lastrowid
            after = {**after, "_history_id": hid}
        _log(conn, household_id, appliance_id, job_id, kind, by, before, after, ts)


def mark_done(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, *, on: date,
              today: date, by: Caller, at: datetime | None = None) -> None:
    """Done on `on` (today or earlier): the household's own last-done date. It clears a snooze."""
    store._owned_appliance(conn, household_id, appliance_id)
    if on > today:
        raise ValueError("done cannot be in the future")
    _change(conn, household_id, appliance_id, job_id, "done", by, at, last_done=on.isoformat(),
            last_done_source="user", snoozed_until=None)


def set_last_done(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, *,
                  source: Literal["user", "never", "unknown"], on: date | None, today: date, by: Caller,
                  at: datetime | None = None) -> None:
    """The answer to "When did you last do this?": a date, "never" or "don't know"."""
    store._owned_appliance(conn, household_id, appliance_id)
    if source == "user":
        if on is None:
            raise ValueError("a user answer needs a date")
        if on > today:
            raise ValueError("last done cannot be in the future")
    elif on is not None:
        raise ValueError(f"{source!r} takes no date")
    _change(conn, household_id, appliance_id, job_id, "last_done", by, at,
            last_done=on.isoformat() if on else None, last_done_source=source)


def snooze(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, *, until: date | None,
           due_on: date | None, today: date, by: Caller, at: datetime | None = None) -> None:
    """Move the job's due date to `until`, which must be after today and after the date it is due now; None clears
    the snooze. Nothing here moves a date earlier."""
    store._owned_appliance(conn, household_id, appliance_id)
    if until is not None:
        if due_on is None:
            raise ValueError("this job has no due date to snooze")
        if until <= today or until <= due_on:
            raise ValueError(f"a snooze only moves it later: pick a date after {max(today, due_on).isoformat()}")
    _change(conn, household_id, appliance_id, job_id, "snooze", by, at,
            snoozed_until=until.isoformat() if until else None)


def set_schedule(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, *,
                 schedule: Schedule | None, by: Caller, at: datetime | None = None) -> None:
    """The household's own interval or window for the job; None goes back to the manual's."""
    store._owned_appliance(conn, household_id, appliance_id)
    if schedule is not None and schedule.kind == "window" and not (1 <= (schedule.min or 0) < (schedule.max or 0)):
        raise ValueError("a window needs 1 <= min < max")
    _change(conn, household_id, appliance_id, job_id, "schedule", by, at,
            schedule=schedule.api() if schedule else None)


def set_assignee(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, *,
                 person_id: str | None, by: Caller, at: datetime | None = None) -> None:
    """Assign the job to a person in this home, or to nobody."""
    store._owned_appliance(conn, household_id, appliance_id)
    if person_id is not None and conn.execute(
            "SELECT 1 FROM people WHERE id = ? AND household_id = ? AND removed_at IS NULL",
            (person_id, household_id)).fetchone() is None:
        raise ValueError("not a person in this home")
    _change(conn, household_id, appliance_id, job_id, "assignee", by, at, assignee=person_id)


def _visible(st: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in st.items() if not k.startswith("_")}


def undo(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, *, by: Caller,
         at: datetime | None = None, own: bool = False) -> None:
    """Revert the job's latest change not yet undone. An undo is itself a change, and is not undoable. With `own`,
    only a change made with the caller's own issued token (by its id, as labels can repeat), so an agent can take
    back its own mistake and nobody else's."""
    store._owned_appliance(conn, household_id, appliance_id)
    row = conn.execute(
        "SELECT * FROM job_change WHERE household_id = ? AND appliance_id = ? AND job_id = ? AND kind != 'undo'"
        " AND undone_by IS NULL ORDER BY id DESC LIMIT 1", (household_id, appliance_id, job_id)).fetchone()
    if row is None:
        raise Conflict("nothing to undo")
    if own and (by.token_id is None or row["by_token"] != by.token_id):
        raise NotOwn(f"the latest change was made by {row['by_label']}, not this key; nothing was undone")
    before, after = json.loads(row["before_json"]), json.loads(row["after_json"])
    now = state(conn, appliance_id, job_id)
    if now != _visible(after):
        raise Conflict("this job changed since; nothing was undone")
    ts = now_iso(at)
    with conn:
        _put(conn, appliance_id, job_id, before, ts)
        if after.get("_history_id"):
            conn.execute("DELETE FROM history WHERE id = ?", (after["_history_id"],))
        uid = _log(conn, household_id, appliance_id, job_id, "undo", by, now, before, ts, undoes=row["id"])
        conn.execute("UPDATE job_change SET undone_by = ? WHERE id = ?", (uid, row["id"]))


def changes(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str,
            limit: int = 20) -> list[dict[str, Any]]:
    """The job's changes (the contract's Change), newest first."""
    rows = conn.execute(
        "SELECT * FROM job_change WHERE household_id = ? AND appliance_id = ? AND job_id = ? ORDER BY id DESC"
        " LIMIT ?", (household_id, appliance_id, job_id, limit)).fetchall()
    return [{"id": r["id"], "at": r["at"], "appliance_id": r["appliance_id"], "job_id": r["job_id"], "kind": r["kind"],
             "by": {"kind": r["by_kind"], "person_id": r["by_person"], "label": r["by_label"]},
             "before": _visible(json.loads(r["before_json"])), "after": _visible(json.loads(r["after_json"])),
             "undone": r["undone_by"] is not None} for r in rows]
