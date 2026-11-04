"""Push one due reminder now, to every phone the household subscribed: for filming, or to see it once more.

    uv run python -m app.feeds.remind_now <household_id> <job_id> [--appliance ID] [--dry-run]

It sends what the timer would send (the card's name, interval and page) and only for a job that is due now, so no
date moves; run it on the host whose store the app serves (FIXIT_DB, else $FIXIT_PRIVATE/store.sqlite). It sends
nothing between 22:00 and 08:00 (Amsterdam). --dry-run prints the notification and sends nothing.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.feeds import push
from app.feeds.push import Subscription
from app.schedule import store
from app.schedule.invites import db_path
from app.schedule.remind import Reminder

TZ = ZoneInfo("Europe/Amsterdam")
QUIET = (22, 8)  # no push from 22:00 until 08:00


def quiet(now: datetime) -> bool:
    h = now.astimezone(TZ).hour
    return h >= QUIET[0] or h < QUIET[1]


def main(argv: list[str] | None = None, now: datetime | None = None) -> None:
    from app.server import checked_card_loader

    ap = argparse.ArgumentParser(prog="remind_now")
    ap.add_argument("household_id")
    ap.add_argument("job_id")
    ap.add_argument("--appliance", help="when the job is on more than one appliance")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    now = now or datetime.now(UTC)
    conn = store.open_store(db_path())
    send: push.Sender
    if args.dry_run:
        def send(sub: Subscription, r: Reminder) -> int:
            return 0
    else:
        if quiet(now):
            raise SystemExit(f"{now.astimezone(TZ):%H:%M} in Amsterdam: no push between 22:00 and 08:00")
        send = push.webpush_sender(push.load_vapid(Path(os.environ.get("FIXIT_VAPID", push.DEFAULT_VAPID))))
    try:
        r, sent = push.send_now(conn, args.household_id, args.job_id, now, checked_card_loader(), send,
                                appliance_id=args.appliance)
    except push.NotDue as e:
        raise SystemExit(str(e)) from None
    p = r.payload()
    print(json.dumps({k: p[k] for k in ("title", "body", "url")}, ensure_ascii=False, indent=1))
    phones = len(push.subscriptions(conn, args.household_id)) + sent.gone
    if args.dry_run:
        print(f"dry run: {phones} subscribed phone(s); nothing sent")
    else:
        print(f"sent to {sent.delivered} of {phones} phone(s)" + (f"; {sent.gone} gone, dropped" if sent.gone else ""))
    if sent.waiting:
        print(f"{len(sent.waiting)} person(s) in their quiet hours: not sent to them now")


if __name__ == "__main__":
    main()
