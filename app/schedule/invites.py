"""Invite codes for the pilot cohort: one household per code, each with its daily cap.

    uv run python -m app.schedule.invites new 8 [--cap 0.45] [--own] [--country nl --lang nl] [--link https://host]
    uv run python -m app.schedule.invites list
    uv run python -m app.schedule.invites token <household_id>   # its own bearer token (the app's)
    uv run python -m app.schedule.invites token <household_id> --rotate
                                               # a new bearer token; the old one stops working, no other token changes
    uv run python -m app.schedule.invites return-link <household_id> https://host
                                               # a single-use link that opens the home on a phone, as its first admin
    uv run python -m app.schedule.invites token <household_id> --kind ha [--read-only] [--label "living room"]
                                               # a new token for its Home Assistant or agent; changes say who made them
    uv run python -m app.schedule.invites door                     # is the open door open, and who said so when
    uv run python -m app.schedule.invites door open --by <name>    # let homes open without a code (POST /households)
    uv run python -m app.schedule.invites door close --by <name>
    uv run python -m app.schedule.invites link new --judges --label "..." --cap-home <usd/day> --total <usd> \
                                               --homes <n> --expires <YYYY-MM-DD> --out <file> [--origin https://host]
    uv run python -m app.schedule.invites link list
    uv run python -m app.schedule.invites link revoke <link_id>

The store is FIXIT_DB (default: $FIXIT_PRIVATE/store.sqlite, the path make pilot reads). Codes print once;
hand each one over in person. The door verb is the only thing that opens the door: it writes a door_log row with who
and when, and the latest row wins (none: shut). A judges' link's caps are whatever its maker passes; its code goes
once into --out (a new 0600 file), never to the terminal, and only its hash is stored. It expires at the end of
--expires, Amsterdam time.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.schedule import door, store

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def db_path() -> Path:
    if os.environ.get("FIXIT_DB"):
        return Path(os.environ["FIXIT_DB"])
    private = Path(os.environ.get("FIXIT_PRIVATE", Path.home() / "Projects/hackathons/fix-it-lens-private"))
    return private / "store.sqlite"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="invites")
    sub = ap.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new")
    new.add_argument("n", type=int)
    new.add_argument("--cap", type=float, default=0.45)
    kind = new.add_mutually_exclusive_group()
    kind.add_argument("--own", action="store_true", help="one of our own two homes (reported apart)")
    kind.add_argument("--test", action="store_true",
                      help="a test household: not in the pilot's counts, sent no reminders, billed to the lane")
    new.add_argument("--country", default="nl")
    new.add_argument("--lang", default="nl")
    new.add_argument("--link", metavar="BASE", help="also print BASE/?invite=<code>")
    sub.add_parser("list")
    tok = sub.add_parser("token", help="print a joined household's bearer token (this terminal only)")
    tok.add_argument("household_id")
    tok.add_argument("--kind", choices=["ha", "agent"], help="issue a new token for this client instead")
    tok.add_argument("--read-only", action="store_true", help="with --kind: it can read but not change anything")
    tok.add_argument("--label", help="with --kind: a name to tell tokens apart")
    tok.add_argument("--rotate", action="store_true",
                     help="replace the household's bearer token (the old one stops working) and print the new one")
    ret = sub.add_parser("return-link", help="a single-use link that opens the household, as its first admin, on a "
                                            "phone (no token on the command line)")
    ret.add_argument("household_id")
    ret.add_argument("base", help="the app's address, e.g. https://host:8443")
    dr = sub.add_parser("door", help="show, open or shut the open door")
    dr.add_argument("state", nargs="?", choices=["open", "close"])
    dr.add_argument("--by", help="with open or close: who decided (kept with the row)")
    link = sub.add_parser("link", help="judges' links")
    lsub = link.add_subparsers(dest="link_cmd", required=True)
    ln = lsub.add_parser("new")
    ln.add_argument("--judges", action="store_true", required=True, help="the only kind of link there is")
    ln.add_argument("--label", required=True)
    ln.add_argument("--cap-home", type=float, required=True, help="USD per home opened from it, per day")
    ln.add_argument("--total", type=float, required=True, help="USD for all its homes together, ever")
    ln.add_argument("--homes", type=int, required=True, help="homes it may open, ever")
    ln.add_argument("--expires", type=date.fromisoformat, required=True, help="last day it works (Amsterdam)")
    ln.add_argument("--out", type=Path, required=True, help="a new file for the code (made 0600)")
    ln.add_argument("--origin", help="also write the link: <origin>/try#k=<code>")
    lsub.add_parser("list")
    lr = lsub.add_parser("revoke")
    lr.add_argument("link_id")
    args = ap.parse_args(argv)
    conn = store.open_store(db_path())
    if args.cmd == "door":
        door_verb(conn, args)
    elif args.cmd == "link":
        link_verb(conn, args)
    elif args.cmd == "new":
        for inv in store.create_invites(conn, args.n, cap_usd_day=args.cap, is_own=args.own, is_test=args.test,
                                        country=args.country, lang=args.lang):
            code = f"{inv.code[:5]}-{inv.code[5:]}"
            link = f"  {args.link.rstrip('/')}/?invite={code}" if args.link else ""
            print(f"{code}  {inv.household_id}{link}")
    elif args.cmd == "return-link":
        hh = conn.execute("SELECT consent_at, deleted_at FROM households WHERE id = ?", (args.household_id,)).fetchone()
        admin = store.first_admin(conn, args.household_id)
        if hh is None or hh["consent_at"] is None or hh["deleted_at"] is not None or admin is None:
            raise SystemExit(f"{args.household_id}: no joined household with an admin by that id")
        code, expires_at = door.new_return_code(conn, args.household_id, str(admin["id"]))
        print(f"{args.base.rstrip('/')}/#return/{code}  (once, until {expires_at:%Y-%m-%d %H:%M} UTC)")
    elif args.cmd == "token":
        q = "SELECT consent_at, deleted_at FROM households WHERE id = ?"
        row = conn.execute(q, (args.household_id,)).fetchone()
        if row is None or row["consent_at"] is None or row["deleted_at"] is not None:
            raise SystemExit(f"{args.household_id}: no joined household by that id")
        secret = store.load_secret(db_path().parent / "secrets/household.secret")
        if args.kind is None:
            if args.read_only or args.label:
                raise SystemExit("--read-only and --label go with --kind")
            print(store.rotate_household_token(conn, secret, args.household_id) if args.rotate
                  else store.current_household_token(conn, secret, args.household_id))
        elif args.rotate:
            raise SystemExit("--rotate replaces the household's own token; an ha or agent token is revoked by its id")
        else:
            label = args.label or {"ha": "Home Assistant", "agent": "agent"}[args.kind]
            _, token = store.issue_token(conn, args.household_id, by_kind=args.kind,
                                         scope="read" if args.read_only else "write", label=label)
            print(token)
    else:
        q = "SELECT id, cap_usd_day, is_own, is_test, consent_at, deleted_at FROM households ORDER BY 1"
        for r in conn.execute(q):
            state = "deleted" if r["deleted_at"] else ("joined " + r["consent_at"] if r["consent_at"] else "unused")
            tag = "own " if r["is_own"] else "test " if r["is_test"] else ""
            print(f"{r['id']}  cap ${r['cap_usd_day']:.2f}  {tag}{state}")


def door_verb(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    if args.state is None:
        if args.by:
            raise SystemExit("--by goes with open or close")
    else:
        if not args.by or not args.by.strip() or len(args.by.strip()) > 60:
            raise SystemExit("say who decided: --by <name> (up to 60 characters)")
        door.set_door(conn, "open" if args.state == "open" else "closed", by=args.by.strip())
    row = door.door(conn)
    print("door: shut (never opened)" if row is None else f"door: {'open' if row['state'] == 'open' else 'shut'}"
          f" since {row['at']}, by {row['by']}")


def link_verb(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    if args.link_cmd == "new":
        if args.cap_home < 0 or args.total < 0 or args.homes < 1:
            raise SystemExit("--cap-home and --total are 0 or more, --homes 1 or more")
        if not 1 <= len(args.label) <= 60:
            raise SystemExit("--label: 1 to 60 characters")
        end = datetime.combine(args.expires + timedelta(days=1), time(0), AMSTERDAM)
        try:  # the file first, so a code never exists that was not written down
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise SystemExit(f"{args.out}: already there; pick a new file") from None
        try:
            lid, code = door.new_judges_link(conn, label=args.label, cap_usd_day_home=args.cap_home,
                                             cap_usd_total=args.total, max_homes=args.homes, expires_at=end)
        except ValueError as e:
            os.close(fd)
            args.out.unlink()
            raise SystemExit(str(e)) from None
        with os.fdopen(fd, "w") as f:
            f.write(f"{args.origin.rstrip('/')}/try#k={code}\n" if args.origin else f"{code}\n")
        print(f"{lid}  {args.label}: ${args.cap_home:.2f}/home/day, ${args.total:.2f} in all, {args.homes} homes,"
              f" until {end.isoformat()}; the code is in {args.out}")
    elif args.link_cmd == "revoke":
        if not door.revoke_link(conn, args.link_id):
            raise SystemExit(f"{args.link_id}: no live link by that id")
        print(f"{args.link_id}: revoked")
    else:
        q = ("SELECT l.*, (SELECT count(*) FROM households h WHERE h.invite_link_id = l.id) AS homes"
             " FROM invite_links l ORDER BY created_at")
        for r in conn.execute(q):
            state = "revoked" if r["revoked_at"] else f"until {r['expires_at']}"
            print(f"{r['id']}  {r['label']}  {r['homes']}/{r['max_homes']} homes  ${r['cap_usd_day_home']:.2f}/home/day"
                  f"  ${r['cap_usd_total']:.2f} in all  {state}")


if __name__ == "__main__":
    main()
