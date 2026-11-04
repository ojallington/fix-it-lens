"""The household's upkeep calendar as an .ics feed (/feeds/<feed_token>.ics), for iPhones and any calendar app.

Each dated upkeep job is an all-day event from its due date (to a window's last day, when the household chose a
window) with a VALARM at the reminder's moment, plus a VTODO for clients that read to-dos. A snoozed job sits on its
snooze date. Unanswered and cycle-counted jobs are left out: they have no date.

Each entry links to the job in the app (`<origin>/#job/<appliance>/<job>`), where it can be marked done or snoozed;
its URL field adds the reminder's query, so the app counts it as opened from the calendar. Following the link
changes no date (only a tap in the app does), and the feed holds no token: the app uses its own.

A calendar polls the feed, so the server never sees an alarm fire. `reminder_sent` (channel `ics`) is logged the
first time the feed is served on or after a reminder's moment: the reminder has reached the household's calendar.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote

from icalendar import Alarm, Calendar, Event, Todo

from app.schedule import people, store
from app.schedule.due import CardLoader, Due, due
from app.schedule.remind import _day, reminder, why_line

PRODID = "-//Fix-It Lens//upkeep schedule//EN"
LINES = {
    "en": {"window": "Any day from {first} to {last}.", "link": "Mark it done or snooze it: {url}"},
    "nl": {"window": "Op een dag naar keuze van {first} tot en met {last}.",
           "link": "Afvinken of uitstellen: {url}"},
}


def job_link(base_url: str, appliance_id: str, job_id: str, reminder_url: str = "/") -> str:
    """The job's sheet in the app. With the reminder's query (the entry's URL field) the app also counts the reminder
    as opened from the calendar; the short form goes in the text people read."""
    q = f"{reminder_url}&ch=ics" if reminder_url != "/" else "/"
    return f"{base_url.rstrip('/')}{q}#job/{quote(appliance_id, safe='')}/{quote(job_id, safe='')}"


def _event(d: Due, now: datetime, base_url: str) -> tuple[Event, Todo]:
    assert d.due_on is not None and d.due_at is not None
    r = reminder(d, now.date())
    url = job_link(base_url, d.appliance_id, d.job_id, r.url)
    last = d.window_end if d.window_end and d.window_end > d.due_on else d.due_on
    t = LINES.get(d.lang, LINES["en"])
    when = t["window"].format(first=_day(d.due_on, d.lang), last=_day(last, d.lang)) + "\n" if last > d.due_on else ""
    short = job_link(base_url, d.appliance_id, d.job_id)
    cited = f"{r.footnote}: “{d.quote}”" if d.manual_cited else why_line(d)
    desc = f"{when}{r.body}\n{cited}\n{t['link'].format(url=short)}"
    ev = Event()
    ev.add("uid", f"{r.key}@fixit")
    ev.add("dtstamp", now)
    ev.add("summary", r.title)
    ev.add("dtstart", d.due_on)
    ev.add("dtend", last + timedelta(days=1))
    ev.add("description", desc)
    ev.add("url", url)
    ev.add("transp", "TRANSPARENT")
    al = Alarm()
    al.add("action", "DISPLAY")
    al.add("description", r.title)
    al.add("trigger", d.due_at)  # an absolute UTC moment: 09:00 local on the due date
    ev.add_component(al)
    todo = Todo()
    todo.add("uid", f"{r.key}-todo@fixit")
    todo.add("dtstamp", now)
    todo.add("summary", r.title)
    if last > d.due_on:
        todo.add("dtstart", d.due_on)
    todo.add("due", last)
    todo.add("description", desc)
    todo.add("url", url)
    return ev, todo


def feed(conn: sqlite3.Connection, household_id: str, now: datetime, load_card: CardLoader, base_url: str, *,
         person_id: str | None = None) -> bytes:
    """The home's upkeep calendar; a person's (`person_id`) carries only what their alerts follow, and nothing
    when they turned the calendar off."""
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Upkeep")
    cal.add("refresh-interval", timedelta(hours=1), parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", "PT1H")
    hh = conn.execute("SELECT is_test FROM households WHERE id = ?", (household_id,)).fetchone()
    is_test = bool(hh and hh["is_test"])  # its feed still shows the dates; no reminder is logged as sent
    for d in due(conn, household_id, now, load_card):
        if d.due_on is None or (person_id is not None and not any(
                p["id"] == person_id for p, _ in people.alerted(conn, household_id, appliance_id=d.appliance_id,
                                                                assignee=d.assignee, channel="calendar"))):
            continue
        ev, todo = _event(d, now, base_url)
        cal.add_component(ev)
        cal.add_component(todo)
        key = store.reminder_key(d.appliance_id, d.job_id, d.due_on)
        if d.is_due(now) and not is_test and not store.reminder_was_sent(conn, household_id, key, "ics"):
            store.record_reminder_sent(conn, household_id, key, "ics", at=now)
    return cal.to_ical()
