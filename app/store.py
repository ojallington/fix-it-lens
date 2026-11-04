"""What a session reads from and writes to the household store (app/schedule/store.py over contracts/store.sql).

app/schedule writes pilot events and job state; this module writes the appliances, history and sessions rows. A session
asks this for:
- who the household is (the bearer token from hello.household), its country, language and daily cap;
- the appliance a reminder opened (card path, alias, HA entities);
- and it reports back: job started, job done (checked), a stop, a voice note, the session's end.

Imports of app/schedule are deferred, so app/ imports without them; the server builds a `Store` only when they are
there.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app import config

if TYPE_CHECKING:
    from app.session import Session


@dataclass(frozen=True)
class Household:
    id: str
    country: str
    lang: str
    cap_usd: float
    lane: str = config.LANE  # the ledger lane its calls bill to: 'pilot' for cohort homes, config.LANE for our own


@dataclass(frozen=True)
class Appliance:
    id: str
    alias: str
    card_path: str | None
    ha_entities: list[str]


class Store:
    def __init__(self, conn: sqlite3.Connection, secret: bytes, ledger: Any = None) -> None:
        self.conn, self.secret, self.ledger = conn, secret, ledger

    def role(self, c: Any) -> str | None:
        """admin, member or viewer for a person's caller; None for an agent, ha or feed token (or a person since
        removed). The household's own token speaks for its first admin."""
        if c.by_kind != "app":
            return None
        if c.person_id is None:
            return "admin"
        p = self.conn.execute("SELECT role, view_only FROM people WHERE id = ? AND removed_at IS NULL",
                              (c.person_id,)).fetchone()
        return None if p is None else "viewer" if p["view_only"] else str(p["role"])

    def acting(self, token: str) -> Any:
        """The household row a token may act for (start a session): its own token, or the app token of a person who
        may change things (a phone back from a return link). None for a view-only or removed person, or another
        kind of token."""
        from app.schedule.store import caller_from_token

        c = caller_from_token(self.conn, self.secret, token)
        return c.household if c is not None and self.role(c) in ("admin", "member") else None

    def household(self, token: str) -> Household | None:
        """The household a token may act for (acting), with its cap; raises BudgetExceeded when it has no room
        today."""
        from app.feeds.caps import assert_household_under_cap, household_cap, household_lane

        row = self.acting(token)
        if row is None:
            return None
        cap = assert_household_under_cap(self.conn, self.ledger, row["id"]) if self.ledger is not None \
            else household_cap(self.conn, row["id"])
        return Household(row["id"], row["country"], row["lang"], cap,
                         household_lane(self.conn, row["id"], config.LANE))

    def appliance(self, household_id: str, appliance_id: str) -> Appliance | None:
        row = self.conn.execute(
            "SELECT a.id, a.alias, a.ha_entities, c.card_path FROM appliances a "
            "LEFT JOIN cards_ref c ON c.appliance_id = a.id"
            " WHERE a.id = ? AND a.household_id = ? AND a.deleted_at IS NULL",
            (appliance_id, household_id)).fetchone()
        if row is None:
            return None
        return Appliance(row["id"], row["alias"], row["card_path"], json.loads(row["ha_entities"] or "[]"))

    def entity_map(self, household_id: str) -> dict[str, list[str]]:
        """alias -> HA entity ids or object-id prefixes; an empty list means the appliance has no HA."""
        rows = self.conn.execute(
            "SELECT alias, ha_entities FROM appliances WHERE household_id = ? AND deleted_at IS NULL", (household_id,))
        return {r["alias"]: json.loads(r["ha_entities"] or "[]") for r in rows}

    # ---- the REST views (api.openapi.yaml: Appliance, HistoryEntry, SessionSummary) ----------------------------

    def appliances(self, household_id: str, appliance_id: str | None = None) -> list[dict[str, Any]]:
        q = ("SELECT a.id, a.alias, a.kind, a.brand, a.model, a.plate_confirmed, c.status FROM appliances a "
             "LEFT JOIN cards_ref c ON c.appliance_id = a.id WHERE a.household_id = ? AND a.deleted_at IS NULL")
        args: tuple[Any, ...] = (household_id,)
        if appliance_id is not None:
            q, args = q + " AND a.id = ?", (household_id, appliance_id)
        return [{"id": r["id"], "alias": r["alias"], "kind": r["kind"], "brand": r["brand"], "model": r["model"],
                 "plate_confirmed": bool(r["plate_confirmed"]), "card_status": r["status"] or "pending"}
                for r in self.conn.execute(q + " ORDER BY a.created_at", args)]

    def remove_appliance(self, household_id: str, appliance_id: str) -> bool:
        """Takes an appliance out of the home's views (DELETE /appliances/{id}): the row stays, with its history,
        sessions and reports, and the card library is not touched. Its name is freed for a new one by keeping the old
        name with the id after it. False when the home has no such appliance."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE appliances SET deleted_at = ?, alias = alias || ' (removed ' || id || ')'"
                " WHERE id = ? AND household_id = ? AND deleted_at IS NULL", (_now(), appliance_id, household_id))
        return cur.rowcount == 1

    def history(self, household_id: str, appliance_id: str | None = None) -> list[dict[str, Any]]:
        q = ("SELECT id, appliance_id, kind, job_id, session_id, text, on_date FROM history WHERE household_id = ?"
             + (" AND appliance_id = ?" if appliance_id is not None else "") + " ORDER BY on_date DESC, id DESC")
        args = (household_id,) if appliance_id is None else (household_id, appliance_id)
        return [dict(r) for r in self.conn.execute(q, args)]

    def add_history(self, household_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO history (household_id, appliance_id, kind, job_id, session_id, text, on_date, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (household_id, entry.get("appliance_id"), entry["kind"], entry.get("job_id"), entry.get("session_id"),
                 entry.get("text"), entry["on_date"], _now()))
        row = self.conn.execute("SELECT id, appliance_id, kind, job_id, session_id, text, on_date FROM history "
                                "WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def sessions(self, household_id: str, session_id: str | None = None) -> list[dict[str, Any]]:
        q = ("SELECT id, appliance_id, job_id, mode, started_at, ended_at, outcome, usd FROM sessions "
             "WHERE household_id = ?" + (" AND id = ?" if session_id is not None else "") + " ORDER BY started_at DESC")
        args = (household_id,) if session_id is None else (household_id, session_id)
        return [dict(r) for r in self.conn.execute(q, args)]

    def session_started(self, s: Session) -> None:
        if s.household is None:
            return
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO sessions (id, household_id, appliance_id, job_id, mode, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (s.session_id, s.household, s.appliance_id, s.job_id, s.mode, _now()))

    def session_ended(self, s: Session, outcome: str, usd: float = 0.0) -> None:
        if s.household is None:
            return
        with self.conn:
            self.conn.execute("UPDATE sessions SET ended_at = ?, outcome = ?, usd = usd + ? WHERE id = ?",
                              (_now(), outcome, usd, s.session_id))


class StoreHooks:
    """The session's Hooks over the store. Without a household or an appliance there is nothing to write."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def _ids(self, s: Session) -> tuple[str, str, str] | None:
        if s.household is None or s.appliance_id is None or s.job_id is None:
            return None
        return s.household, s.appliance_id, s.job_id

    def job_started(self, s: Session) -> None:
        from app.schedule.store import record_job_started

        ids = self._ids(s)
        if ids is not None:
            record_job_started(self.store.conn, *ids, s.session_id)

    def job_done_checked(self, s: Session) -> None:
        from app.schedule.store import record_job_done_checked

        ids = self._ids(s)
        if ids is not None:
            record_job_done_checked(self.store.conn, *ids, s.session_id, on=datetime.now(UTC).date())

    def job_done_by_person(self, s: Session) -> None:
        from app.schedule.store import record_job_done_by_person

        ids = self._ids(s)
        if ids is not None:
            record_job_done_by_person(self.store.conn, *ids, s.session_id, on=datetime.now(UTC).date())

    def history_text(self, s: Session) -> str | None:
        """This appliance's history for the guide's prefix (Guide._prefix asks for it once, at the session start)."""
        from app.guide import render_history

        if s.household is None or s.appliance_id is None:
            return None
        return render_history(self.store.history(s.household, s.appliance_id))

    def history(self, s: Session, kind: str, text: str | None) -> None:
        """A voice note or a stop. The text is what passed G0 or a rule id and evidence; never a held sentence."""
        if s.household is None:
            return
        with self.store.conn:
            self.store.conn.execute(
                "INSERT INTO history (household_id, appliance_id, kind, job_id, session_id, text, on_date, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (s.household, s.appliance_id, kind, s.job_id, s.session_id, text, datetime.now(UTC).date().isoformat(),
                 _now()))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
