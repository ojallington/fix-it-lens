"""The cap per home: each household's model spend today (UTC) stays under households.cap_usd_day.

The ledger enforces it inside `calls/` when a call carries `household_cap_usd` (contracts 1.2.0); call
`assert_household_under_cap` before starting work that will call a model for a household (an ingest, a session), so
a home past its cap is refused before any call is made.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from calls.errors import BudgetExceeded
from calls.ledger import Ledger

PILOT_LANE = "pilot"
# The least any model call costs; a check for "room for anything" asks for this much.
MIN_CALL_USD = 0.001


def household_lane(conn: sqlite3.Connection, household_id: str, own_lane: str = "l1") -> str:
    """Pilot homes bill to the pilot budget; the project's own homes and test households bill to their own."""
    row = conn.execute("SELECT is_own, is_test FROM households WHERE id = ?", (household_id,)).fetchone()
    return own_lane if row and (row["is_own"] or row["is_test"]) else PILOT_LANE


def household_cap(conn: sqlite3.Connection, household_id: str) -> float:
    row = conn.execute("SELECT cap_usd_day FROM households WHERE id = ? AND deleted_at IS NULL",
                       (household_id,)).fetchone()
    if row is None:
        raise BudgetExceeded(f"household {household_id}: unknown")
    return float(row["cap_usd_day"])


def assert_household_under_cap(conn: sqlite3.Connection, ledger: Ledger, household_id: str, *,
                               estimate_usd: float = MIN_CALL_USD, lane: str | None = None) -> float:
    """Raise BudgetExceeded if this household (or its lane, or the total) has no room for `estimate_usd`.
    Returns the cap, for the call's `household_cap_usd`."""
    cap = household_cap(conn, household_id)
    if cap <= 0:
        raise BudgetExceeded(f"household {household_id}: cap is 0")
    ledger.check(lane or household_lane(conn, household_id), max(estimate_usd, MIN_CALL_USD), household=household_id,
                 household_cap_usd=cap)
    return cap


def households_spend(ledger: Ledger, household_ids: Iterable[str]) -> float:
    """What these households have spent, ever: a judges' link's total, over the homes it opened."""
    ids = set(household_ids)
    if not ids or not ledger.path.exists():
        return 0.0
    usd = 0.0
    with ledger.path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a line still being written
            if row.get("household") in ids:
                usd += float(row.get("usd") or 0.0)
    return usd
