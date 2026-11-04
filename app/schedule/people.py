"""The people in a home (contracts: Person, Invite, Alerts), as Google Home and Apple Home have them: an admin may do
everything; a member sees everything and changes the schedule; a view-only member reads and sets their own alerts.

A home keeps at least one admin. A people invite (people_invites) brings someone into an existing home; the pilot's
invite codes open new households and stay apart. An invite code is shown once, to the admin who made it, and only its
hash is stored. Removing a person revokes their tokens and push subscriptions and unassigns their jobs.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal

from app.schedule.store import now_iso

Role = Literal["admin", "member"]


class PeopleError(Exception):
    """A refused change: `error` is the contract's code (last_admin, name_taken, not_found, forbidden, invalid)."""
    def __init__(self, error: str, detail: str) -> None:
        super().__init__(detail)
        self.error, self.detail = error, detail


def _sha(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def person_json(row: sqlite3.Row, me: str | None) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "role": row["role"], "view_only": bool(row["view_only"]),
            "me": row["id"] == me}


def person(conn: sqlite3.Connection, household_id: str, person_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM people WHERE id = ? AND household_id = ? AND removed_at IS NULL",
        (person_id, household_id)).fetchone()
    return row


def people(conn: sqlite3.Connection, household_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM people WHERE household_id = ? AND removed_at IS NULL ORDER BY created_at, rowid",
                        (household_id,)).fetchall()


def _admins(conn: sqlite3.Connection, household_id: str) -> int:
    return int(conn.execute("SELECT count(*) FROM people WHERE household_id = ? AND role = 'admin'"
                            " AND removed_at IS NULL", (household_id,)).fetchone()[0])


def _name_free(conn: sqlite3.Connection, household_id: str, name: str, but: str | None = None) -> bool:
    return conn.execute("SELECT 1 FROM people WHERE household_id = ? AND name = ? AND removed_at IS NULL AND id != ?",
                        (household_id, name, but or "")).fetchone() is None


def change_person(conn: sqlite3.Connection, household_id: str, actor: sqlite3.Row, person_id: str, *,
                  name: str | None = None, role: Role | None = None, view_only: bool | None = None) -> sqlite3.Row:
    """Rename (an admin, or the person themselves), or change role or view_only (an admin)."""
    target = person(conn, household_id, person_id)
    if target is None:
        raise PeopleError("not_found", "no such person in this home")
    admin = actor["role"] == "admin"
    if (role is not None or view_only is not None) and not admin:
        raise PeopleError("forbidden", "only an admin changes a role")
    if name is not None and not admin and actor["id"] != person_id:
        raise PeopleError("forbidden", "only an admin, or the person themselves, renames")
    new_role = role or target["role"]
    new_view = bool(target["view_only"]) if view_only is None else view_only
    if role == "admin" and view_only is None:
        new_view = False  # an admin is never view-only
    if new_role == "admin" and new_view:
        raise PeopleError("invalid", "an admin is never view-only")
    if target["role"] == "admin" and new_role != "admin" and _admins(conn, household_id) <= 1:
        raise PeopleError("last_admin", "a home keeps at least one admin")
    if name is not None and not _name_free(conn, household_id, name, but=person_id):
        raise PeopleError("name_taken", "someone in this home already has that name")
    with conn:
        conn.execute("UPDATE people SET name = ?, role = ?, view_only = ? WHERE id = ?",
                     (name or target["name"], new_role, int(new_view), person_id))
    row = person(conn, household_id, person_id)
    assert row is not None
    return row


def remove_person(conn: sqlite3.Connection, household_id: str, actor: sqlite3.Row, person_id: str,
                  at: datetime | None = None) -> None:
    """Remove a person (an admin, or the person leaving): their tokens and push go, their jobs are unassigned."""
    target = person(conn, household_id, person_id)
    if target is None:
        raise PeopleError("not_found", "no such person in this home")
    if actor["role"] != "admin" and actor["id"] != person_id:
        raise PeopleError("forbidden", "only an admin removes someone else")
    if target["role"] == "admin" and _admins(conn, household_id) <= 1:
        raise PeopleError("last_admin", "a home keeps at least one admin")
    ts = now_iso(at)
    with conn:
        conn.execute("UPDATE people SET removed_at = ? WHERE id = ?", (ts, person_id))
        conn.execute("UPDATE household_tokens SET revoked_at = ? WHERE person_id = ? AND revoked_at IS NULL",
                     (ts, person_id))
        conn.execute("DELETE FROM push_subscriptions WHERE person_id = ?", (person_id,))
        conn.execute("DELETE FROM return_codes WHERE person_id = ?", (person_id,))
        conn.execute("UPDATE job_schedule SET assignee = NULL WHERE assignee = ? AND appliance_id IN"
                     " (SELECT id FROM appliances WHERE household_id = ?)", (person_id, household_id))


# ---- invites into a home ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Invite:
    code: str
    role: Role
    view_only: bool
    email: str | None
    expires_at: datetime


def new_invite(conn: sqlite3.Connection, household_id: str, by: str, *, role: Role, view_only: bool = False,
               email: str | None = None, expires_in_days: int = 7, at: datetime | None = None) -> Invite:
    if role == "admin" and view_only:
        raise PeopleError("invalid", "an admin is never view-only")
    now = (at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    expires = now + timedelta(days=expires_in_days)
    code = secrets.token_urlsafe(18)
    with conn:
        conn.execute("INSERT INTO people_invites (code_sha256, household_id, role, view_only, email, created_by,"
                     " created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (_sha(code), household_id, role, int(view_only), email, by, now_iso(now), now_iso(expires)))
    return Invite(code, role, view_only, email, expires)


def join(conn: sqlite3.Connection, code: str, name: str, at: datetime | None = None) -> tuple[str, str]:
    """Join a home with a people invite: (household_id, person_id). A bad, used or expired invite is not_found;
    a name already in the home is name_taken, and leaves the invite unused."""
    ts = now_iso(at)
    inv = conn.execute(
        "UPDATE people_invites SET used_at = ? WHERE code_sha256 = ? AND used_at IS NULL AND expires_at > ?"
        " AND household_id IN (SELECT id FROM households WHERE deleted_at IS NULL AND consent_at IS NOT NULL)"
        " RETURNING code_sha256, household_id, role, view_only", (ts, _sha(code), ts)).fetchone()
    if inv is None:
        raise PeopleError("not_found", "a bad, used or expired invite")
    hid = str(inv["household_id"])
    if not _name_free(conn, hid, name):
        conn.execute("UPDATE people_invites SET used_at = NULL WHERE code_sha256 = ?", (inv["code_sha256"],))
        raise PeopleError("name_taken", "someone in this home already has that name")
    pid = "p-" + secrets.token_hex(8)
    with conn:
        conn.execute("INSERT INTO people (id, household_id, name, role, view_only, created_at)"
                     " VALUES (?, ?, ?, ?, ?, ?)", (pid, hid, name, inv["role"], inv["view_only"], ts))
        conn.execute("UPDATE people_invites SET used_by = ? WHERE code_sha256 = ?", (pid, inv["code_sha256"]))
    return hid, pid


# ---- one person's alerts ----------------------------------------------------------------------------------------

def alerts(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    """The contract's Alerts: the person's row, or the defaults (push and calendar on, hub on, every item, no quiet
    hours)."""
    row = conn.execute("SELECT * FROM person_alerts WHERE person_id = ?", (person_id,)).fetchone()
    follows = [r[0] for r in conn.execute("SELECT appliance_id FROM person_follows WHERE person_id = ? ORDER BY 1",
                                          (person_id,))]
    if row is None:
        return {"push": True, "calendar": True, "hub": True, "items": "all", "follows": follows, "quiet": None}
    quiet = None if row["quiet_from"] is None else {"from": row["quiet_from"], "to": row["quiet_to"]}
    return {"push": bool(row["push"]), "calendar": bool(row["calendar"]), "hub": bool(row["hub"]),
            "items": row["items"], "follows": follows, "quiet": quiet}


def set_alerts(conn: sqlite3.Connection, household_id: str, person_id: str, *, push: bool, calendar: bool, hub: bool,
               items: Literal["all", "chosen"], follows: list[str], quiet: tuple[str, str] | None) -> None:
    """Set one person's own alerts. Every followed appliance must be in their home."""
    if len(set(follows)) != len(follows):
        raise PeopleError("invalid", "an appliance is followed twice")
    if follows:
        q = ",".join("?" * len(follows))  # placeholders only; values are bound
        mine = {r[0] for r in conn.execute(
            f"SELECT id FROM appliances WHERE household_id = ? AND deleted_at IS NULL AND id IN ({q})",
            (household_id, *follows))}
        if mine != set(follows):
            raise PeopleError("invalid", "an appliance in follows is not in this home")
    with conn:
        conn.execute("INSERT INTO person_alerts (person_id, push, calendar, hub, items, quiet_from, quiet_to)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (person_id) DO UPDATE SET push = excluded.push,"
                     " calendar = excluded.calendar, hub = excluded.hub, items = excluded.items,"
                     " quiet_from = excluded.quiet_from, quiet_to = excluded.quiet_to",
                     (person_id, int(push), int(calendar), int(hub), items, quiet and quiet[0], quiet and quiet[1]))
        conn.execute("DELETE FROM person_follows WHERE person_id = ?", (person_id,))
        conn.executemany("INSERT INTO person_follows (person_id, appliance_id) VALUES (?, ?)",
                         [(person_id, a) for a in follows])


# ---- who a reminder reaches ---------------------------------------------------------------------------------------

def quiet_now(a: dict[str, Any], local: time) -> bool:
    """Whether `local` (household wall time) falls in the person's quiet hours. From 22:00 to 07:00 runs over
    midnight; a span that starts where it ends is no quiet at all."""
    q = a["quiet"]
    if q is None or q["from"] == q["to"]:
        return False
    start, end = time.fromisoformat(q["from"]), time.fromisoformat(q["to"])
    return start <= local < end if start < end else (local >= start or local < end)


def alerted(conn: sqlite3.Connection, household_id: str, *, appliance_id: str, assignee: str | None,
            channel: Literal["push", "calendar"]) -> list[tuple[sqlite3.Row, dict[str, Any]]]:
    """The people a job's alert reaches on a channel, with their alerts: an assigned job its assignee only, an
    unassigned one everyone who follows its item. Quiet hours are the caller's to apply."""
    out = []
    for p in people(conn, household_id):
        a = alerts(conn, p["id"])
        if not a[channel]:
            continue
        if assignee is not None:
            if p["id"] != assignee:
                continue
        elif a["items"] == "chosen" and appliance_id not in a["follows"]:
            continue
        out.append((p, a))
    return out
