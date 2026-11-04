"""The card library: which card file an appliance is linked to, whether other homes get that card, the reports people
file on it, and "this isn't my appliance".

- A card reaches other homes only when its manual was fetched from the maker's own site and the manual's own model
  list covers the model, or when a reviewer links it by hand (`ingest.flow.share_card`). An upload, a card whose manual
  lists no models or other models, and a fetched manual that differs from the shared card's stay with the home that
  built them, with their reason (CardRecord.private_reason).
- One library row per card file: a new manual for the same model is a new file and a new row (a new version), never a
  silent swap.
- A report never edits a card. A reviewer reads it and closes it with `python -m app.library`, and a correction lands
  only as a new card version that passes the same checks. A note stays with the home that wrote it and the reviewers;
  other homes see counts.
- "Not mine" unlinks the card for that home only, and that card file is never linked to that appliance again.

    python -m app.library list                       # open reports (notes G0 holds are marked) and cards to review
    python -m app.library link CARD --by NAME        # share a card whose manual lists no models, after reading it
    python -m app.library replace CARD --by NAME     # a reviewed new version replaces the shared card
    python -m app.library close REPORT --resolution "fixed in a new card version"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app import config

OPEN_REPORTS_MAX = 20
Covers = Literal["exact", "pattern", "none"]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ref(path: Path) -> str:
    """A card file as the store keeps it (cards_ref.card_path): relative to the repo when under it."""
    p = path.resolve()
    return str(p.relative_to(config.ROOT.resolve())) if config.ROOT.resolve() in p.parents else str(p)


def resolve(rel: str) -> Path:
    return (config.ROOT / rel).resolve()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def covers_of(card: dict[str, Any], model: str, *, shared_place: bool) -> Covers:
    """How the manual's own model list covers the model. A shared or repo card is found only by its own model code,
    its aliases or a pattern its manual prints, so with no list it is the model's own card."""
    from ingest import models

    got = models.covers(card["manual"].get("models", []), model)
    return got if got is not None else ("exact" if shared_place else "none")


def _enter(conn: sqlite3.Connection, path: Path, card: dict[str, Any], model: str, *, shared: bool,
           reason: str | None, built_by: str | None) -> None:
    """The library row for a card file; a file whose bytes changed is a new build of that row."""
    upload = card["manual"].get("source") == "household_upload"
    if upload:
        shared, reason = False, "uploaded"
    cov = covers_of(card, model, shared_place=shared)
    if shared and cov == "none":  # never shared without a model list that covers it or a review
        shared = False
    if unconfirmed_codes(path) and not upload:  # codes read from a drawing wait for a person before any sharing
        shared, reason = False, "codes_unconfirmed"
    elif unaccepted_steps(path) and not upload:  # so do the restore steps ingest added
        shared, reason = False, "steps_unaccepted"
    if not shared and reason is None:
        reason = ("model_not_in_list" if card["manual"].get("models") else "no_model_list") if cov == "none" \
            else "version_conflict"
    conn.execute(
        "INSERT INTO card_library (card_path, brand, model, card_sha256, origin, covers, shared, reason, built_by,"
        " built_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (card_path) DO UPDATE SET"
        " card_sha256 = excluded.card_sha256, built_at = excluded.built_at"
        " WHERE card_library.card_sha256 != excluded.card_sha256",
        (ref(path), str(card["brand"]).lower(), str(card["model"]), _sha(path),
         "household_upload" if upload else "maker_site", cov, int(shared), None if shared else reason,
         built_by, _now()))


def _check_record(path: Path) -> dict[str, Any]:
    from ingest import check_pr

    rec_file = check_pr.record_path(path)
    return json.loads(rec_file.read_text()) if rec_file.exists() else {}


def unconfirmed_codes(path: Path) -> list[str]:
    """The fault codes in the card's check record that were read from a drawing and no person has confirmed."""
    from ingest import check_pr

    return check_pr.unconfirmed(_check_record(path))


def unaccepted_steps(path: Path) -> list[str]:
    """Our own restore steps ingest added (job/step) that no person has accepted yet."""
    from ingest import check_pr

    return check_pr.unaccepted(_check_record(path))


def unlinked(conn: sqlite3.Connection, appliance_id: str, path: Path) -> bool:
    return conn.execute("SELECT 1 FROM card_unlinks WHERE appliance_id = ? AND card_path = ?",
                        (appliance_id, ref(path))).fetchone() is not None


def link(conn: sqlite3.Connection, appliance_id: str, household_id: str, path: Path, model: str) -> Path | None:
    """The card file this appliance is linked to, now that ingest found or built `path` for it: the shared card when
    the home's new card may be shared (or is the same version), else the home's own. None when this home said that
    card is not its appliance."""
    from ingest import flow
    from ingest.cards import read_card

    card = read_card(path)
    with conn:
        if flow.household_of(path) == household_id:
            try:
                out: flow.Shared | flow.NotShared = flow.share_card(path)
            except FileNotFoundError:  # no private folder to share into
                out = flow.NotShared("version_conflict", "no shared folder")
            if isinstance(out, flow.Shared):
                path = out.path
                _enter(conn, path, read_card(path), model, shared=True, reason=None, built_by=household_id)
            else:
                _enter(conn, path, card, model, shared=False, reason=out.reason, built_by=household_id)
        else:
            _enter(conn, path, card, model, shared=True, reason=None, built_by=None)
    return None if unlinked(conn, appliance_id, path) else path


# ---- reports and "not mine" ----------------------------------------------------------------------------------------

class TooMany(Exception):
    """This home has OPEN_REPORTS_MAX open reports."""


def report_json(row: sqlite3.Row) -> dict[str, Any]:
    """The contract's Report."""
    return {"id": row["id"], "appliance_id": row["appliance_id"], "kind": row["kind"], "job_id": row["job_id"],
            "step_id": row["step_id"], "note": row["note"], "page": row["page"], "card_sha256": row["card_sha256"],
            "status": row["status"], "resolution": row["resolution"], "created_at": row["created_at"],
            "closed_at": row["closed_at"],
            "by": {"kind": "app", "person_id": row["by_person"], "label": row["by_label"]}}


def _file(conn: sqlite3.Connection, *, household_id: str, appliance_id: str, card_path: str, card_sha256: str,
          kind: str, by_person: str | None, by_label: str, note: str | None = None, job_id: str | None = None,
          step_id: str | None = None, page: int | None = None) -> str:
    n = conn.execute("SELECT count(*) FROM card_reports WHERE household_id = ? AND status = 'open'",
                     (household_id,)).fetchone()[0]
    if n >= OPEN_REPORTS_MAX:
        raise TooMany
    rid = "r_" + uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO card_reports (id, household_id, appliance_id, card_path, card_sha256, kind, job_id, step_id, note,"
        " page, by_person, by_label, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, household_id, appliance_id, card_path, card_sha256, kind, job_id, step_id, note, page, by_person,
         by_label, _now()))
    return rid


def linked(conn: sqlite3.Connection, appliance_id: str) -> sqlite3.Row | None:
    """The appliance's linked card (cards_ref) when there is one and its file is in the library."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT c.card_path, c.card_sha256, a.model FROM cards_ref c JOIN appliances a ON a.id = c.appliance_id"
        " WHERE c.appliance_id = ? AND c.card_path != '' AND c.status IN ('checked', 'draft')",
        (appliance_id,)).fetchone()
    return row


def ensure_entered(conn: sqlite3.Connection, card_path: str, model: str, household_id: str) -> None:
    """A card linked before the library kept rows gets its row on first use, so a report can name it."""
    from ingest import flow
    from ingest.cards import read_card

    if conn.execute("SELECT 1 FROM card_library WHERE card_path = ?", (card_path,)).fetchone() is None:
        path = resolve(card_path)
        own = flow.household_of(path) == household_id
        with conn:
            _enter(conn, path, read_card(path), model, shared=not own, reason=None,
                   built_by=household_id if own else None)


def report(conn: sqlite3.Connection, *, household_id: str, appliance_id: str, kind: str, by_person: str | None,
           by_label: str, note: str | None, job_id: str | None, step_id: str | None,
           page: int | None) -> dict[str, Any] | None:
    """File a report on the card linked to the appliance now; None when nothing is linked. The card never changes."""
    ln = linked(conn, appliance_id)
    if ln is None:
        return None
    ensure_entered(conn, ln["card_path"], ln["model"], household_id)
    with conn:
        rid = _file(conn, household_id=household_id, appliance_id=appliance_id, card_path=ln["card_path"],
                    card_sha256=ln["card_sha256"], kind=kind, by_person=by_person, by_label=by_label, note=note,
                    job_id=job_id, step_id=step_id, page=page)
    return report_json(conn.execute("SELECT * FROM card_reports WHERE id = ?", (rid,)).fetchone())


def not_mine(conn: sqlite3.Connection, *, household_id: str, appliance_id: str, by_person: str | None, by_label: str,
             note: str | None) -> bool:
    """"This isn't my appliance": a wrong_appliance report, the card unlinked for this home only (its jobs leave /due;
    history and last-done dates stay), and that card file never linked to this appliance again. False when nothing
    is linked."""
    ln = linked(conn, appliance_id)
    if ln is None:
        return False
    ensure_entered(conn, ln["card_path"], ln["model"], household_id)
    with conn:
        rid = _file(conn, household_id=household_id, appliance_id=appliance_id, card_path=ln["card_path"],
                    card_sha256=ln["card_sha256"], kind="wrong_appliance", by_person=by_person, by_label=by_label,
                    note=note)
        conn.execute("INSERT OR IGNORE INTO card_unlinks (appliance_id, card_path, report_id, unlinked_at)"
                     " VALUES (?, ?, ?, ?)", (appliance_id, ln["card_path"], rid, _now()))
        conn.execute("UPDATE cards_ref SET card_path = '', card_sha256 = '', status = 'failed', checked_at = NULL"
                     " WHERE appliance_id = ?", (appliance_id,))
    return True


def said_not_mine(conn: sqlite3.Connection, appliance_id: str) -> bool:
    return conn.execute("SELECT 1 FROM card_unlinks WHERE appliance_id = ?", (appliance_id,)).fetchone() is not None


def reports(conn: sqlite3.Connection, household_id: str, appliance_id: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM card_reports WHERE household_id = ?" + (" AND appliance_id = ?" if appliance_id else "")
    args = (household_id, appliance_id) if appliance_id else (household_id,)
    return [report_json(r) for r in conn.execute(q + " ORDER BY created_at DESC, rowid DESC", args)]


# ---- how a card was checked (CardRecord) ---------------------------------------------------------------------------

VERDICTS = "first_time/verdicts.jsonl"  # FIXIT_PRIVATE: one line per read of a built card, the last one counts
NOT_A_PERSON = frozenset({"builder"})  # the build's own read, not a person's


def person_read(card_path: str, card_sha256: str) -> str | None:
    """The day a person read this card and found it usable, from the reads kept in FIXIT_PRIVATE: the latest line for
    the card, and only while it names these very bytes. None when nobody has, or the folder is not there."""
    priv = config.private_dir()
    path = priv / VERDICTS if priv is not None else None
    if path is None or not path.is_file():
        return None
    latest: dict[str, Any] | None = None
    for line in path.read_text().splitlines():
        if line.strip():
            v = json.loads(line)
            if v.get("card") == card_path:
                latest = v
    if latest is None or latest.get("card_sha256") != card_sha256 or latest.get("verdict") != "usable" \
            or not latest.get("verdict_by") or latest.get("verdict_by") in NOT_A_PERSON:
        return None
    return str(latest.get("at") or "") or None

def record(conn: sqlite3.Connection, appliance_id: str, household_id: str) -> dict[str, Any] | None:
    """The contract's CardRecord for the card linked to the appliance, from the library, the check record and the
    store only; None when nothing is linked."""
    from ingest import check_pr

    ln = linked(conn, appliance_id)
    if ln is None:
        return None
    ensure_entered(conn, ln["card_path"], ln["model"], household_id)
    row = conn.execute("SELECT * FROM card_library WHERE card_path = ?", (ln["card_path"],)).fetchone()
    version = conn.execute("SELECT count(*) FROM card_library WHERE brand = ? AND model = ? AND built_at <= ?",
                           (row["brand"], row["model"], row["built_at"])).fetchone()[0]
    homes = conn.execute(
        "SELECT count(DISTINCT a.household_id) FROM cards_ref c JOIN appliances a ON a.id = c.appliance_id"
        " JOIN households h ON h.id = a.household_id WHERE c.card_path = ? AND c.card_sha256 = ?"
        " AND h.is_test = 0 AND h.deleted_at IS NULL AND a.deleted_at IS NULL",
        (ln["card_path"], ln["card_sha256"])).fetchone()[0]
    rec_file = check_pr.record_path(resolve(ln["card_path"]))
    quotes = json.loads(rec_file.read_text()).get("quotes", []) if rec_file.exists() else []
    open_ = conn.execute(
        "SELECT count(*) AS total, coalesce(sum(kind = 'unsafe'), 0) AS safety FROM card_reports"
        " WHERE card_path = ? AND card_sha256 = ? AND status = 'open'", (ln["card_path"], ln["card_sha256"])).fetchone()
    at = row["linked_by_review_at"] or person_read(ln["card_path"], ln["card_sha256"])
    return {"card_sha256": ln["card_sha256"], "version": max(1, version),
            "built_at": row["built_at"].replace("+00:00", "Z"), "shared": bool(row["shared"]),
            "private_reason": None if row["shared"] else row["reason"],
            "reviewed": {"by_person": at is not None, "at": at[:10] if at else None},
            "homes": max(1, homes),
            "quotes": {"checked": len(quotes), "passed": sum(1 for q in quotes if q.get("ok"))},
            "reports_open": {"total": open_["total"], "safety": open_["safety"]},
            "unconfirmed_codes": len(unconfirmed_codes(resolve(ln["card_path"]))),
            "unaccepted_steps": len(unaccepted_steps(resolve(ln["card_path"])))}


# ---- the reviewers' side (a server CLI, never an API) --------------------------------------------------------------

def review_link(conn: sqlite3.Connection, card_path: str, by: str, *, replace: bool = False) -> str:
    """Share a home's card after a person read it against the manual: one whose manual lists no models (`link`), or
    a new version that replaces the shared card (`replace`, which archives the old one). Recorded as linked by
    review, who and when; code alone never does this."""
    from ingest import flow
    from ingest.cards import read_card

    if not by.strip():
        return "a review needs the reviewer's name"
    path = resolve(card_path)
    if not path.exists():
        return f"no card at {card_path}"
    card = read_card(path)
    out = flow.replace_shared(path, by) if replace else flow.share_card(path, reviewed_by=by)
    if isinstance(out, flow.NotShared):
        return f"not shared: {out.reason} {out.detail}".strip()
    target, at = ref(out.path), _now()
    with conn:
        if out.archived is not None:
            conn.execute("UPDATE card_library SET card_path = ? WHERE card_path = ?", (ref(out.archived), target))
        cov = covers_of(card, card["model"], shared_place=False)
        conn.execute(
            "INSERT OR REPLACE INTO card_library (card_path, brand, model, card_sha256, origin, covers, shared, reason,"
            " built_by, built_at, linked_by_review_by, linked_by_review_at)"
            " VALUES (?, ?, ?, ?, 'maker_site', ?, 1, NULL, NULL, ?, ?, ?)",
            (target, str(card["brand"]).lower(), str(card["model"]), _sha(out.path), cov, at,
             by.strip() if cov == "none" else None, at if cov == "none" else None))
    return f"shared as {target}" + (f", the old version archived as {ref(out.archived)}" if out.archived else "")


def close(conn: sqlite3.Connection, report_id: str, resolution: str) -> bool:
    if not resolution.strip():
        return False
    with conn:
        cur = conn.execute("UPDATE card_reports SET status = 'closed', resolution = ?, closed_at = ?"
                           " WHERE id = ? AND status = 'open'", (resolution.strip(), _now(), report_id))
    return cur.rowcount == 1


def review_list(conn: sqlite3.Connection) -> list[str]:
    """Open reports, safety first, each note marked when G0 holds it (a reviewer reads it whole; no home but its own
    ever sees it), then the cards waiting for a person: no model list, a version that differs from the shared card's,
    or fault codes read from a drawing or restore steps ingest added, with the command that confirms them (the only
    writer of that confirmation)."""
    import rules

    out: list[str] = []
    for r in conn.execute("SELECT r.*, h.country FROM card_reports r JOIN households h ON h.id = r.household_id"
                          " WHERE r.status = 'open' ORDER BY r.kind != 'unsafe', r.created_at"):
        held = ""
        if r["note"] and isinstance(rules.check_sentence(r["note"], "en", None, None, country=r["country"]),
                                    rules.Hold):
            held = " [note held by G0]"
        where = " ".join(f"{k}={r[k]}" for k in ("job_id", "step_id", "page") if r[k] is not None)
        out.append(f"{r['id']} {r['kind']} {r['card_path']}@{r['card_sha256'][:12]} {where}{held}\n"
                   f"    {r['note'] or ''}".rstrip())
    for c in conn.execute("SELECT * FROM card_library WHERE shared = 0 AND reason IN ('no_model_list',"
                          " 'version_conflict', 'codes_unconfirmed', 'steps_unaccepted') ORDER BY built_at"):
        line = f"card {c['card_path']} ({c['brand']} {c['model']}): {c['reason']}"
        if c["reason"] in ("codes_unconfirmed", "steps_unaccepted"):
            path = resolve(c["card_path"])
            waiting = unconfirmed_codes(path) + unaccepted_steps(path)
            line += f" {', '.join(waiting)}; a person confirms them with: uv run python -m tools.confirm_codes {path}"
        out.append(line)
    return out


def main(argv: list[str] | None = None) -> int:
    from app.schedule.invites import db_path
    from app.schedule.store import open_store

    ap = argparse.ArgumentParser(prog="python -m app.library", description="Review the card library.")
    ap.add_argument("--db", default=str(db_path()), help="the store (default: the server's, FIXIT_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("link", "replace"):
        p = sub.add_parser(name)
        p.add_argument("card")
        p.add_argument("--by", required=True)
    p = sub.add_parser("close")
    p.add_argument("report")
    p.add_argument("--resolution", required=True)
    args = ap.parse_args(argv)
    if not Path(args.db).exists():
        print(f"no store at {args.db}", file=sys.stderr)
        return 2
    conn = open_store(args.db)
    if args.cmd == "list":
        print("\n".join(review_list(conn)) or "nothing to review")
        return 0
    if args.cmd in ("link", "replace"):
        said = review_link(conn, args.card, args.by, replace=args.cmd == "replace")
        print(said)
        return 0 if said.startswith("shared") else 1
    ok = close(conn, args.report, args.resolution)
    print("closed" if ok else "no open report with that id")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
