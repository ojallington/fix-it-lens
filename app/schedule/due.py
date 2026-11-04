"""`due(household, now)`: every upkeep job's due date, computed by code from the card's interval and `last_done`.

Only cards whose citation check passed give jobs: a job whose interval quote is unchecked or failed is not in the
schedule at all, so no reminder can carry it. The model never touches a due date.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from app.schedule.dates import add_interval, household_tz, local_today, reminder_at
from app.schedule.store import LastDoneSource
from contracts.models.card import Card, Job, RefQuote

# Reads a card at cards_ref.card_path with `checks` filled by the card loader. None if it cannot be read.
CardLoader = Callable[[str], Card | None]

Reason = Literal["scheduled", "never", "unknown", "unanswered", "cycle"]
Status = Literal["upcoming", "due", "overdue", "none"]


@dataclass(frozen=True)
class Schedule:
    """The household's own schedule for a job (job_schedule), beside the manual's: a fixed interval, or a window
    that is due from last + min and overdue after last + max. Never a cycle count."""
    kind: Literal["every", "window"]
    unit: Literal["day", "week", "month", "year"]
    every: int | None = None
    min: int | None = None
    max: int | None = None

    def api(self) -> dict[str, object]:
        if self.kind == "every":
            return {"kind": "every", "every": self.every, "unit": self.unit}
        return {"kind": "window", "min": self.min, "max": self.max, "unit": self.unit}


@dataclass(frozen=True)
class AfterNote:
    """A job from Job.after that the household's history shows done since this job was last done (the
    storyboard's boiler line). It adds a clause to the reminder and never moves the date."""
    job_id: str
    job_title: str
    history_text: str | None     # what the history row says, e.g. "bled two radiators"
    on: date
    because: str                 # the card's own short line, e.g. "Bleeding drops the pressure"
    page: int                    # the page of the quote that backs `because`


@dataclass(frozen=True)
class Due:
    household_id: str
    appliance_id: str
    alias: str
    job_id: str
    title: str
    due_on: date | None          # None: unanswered, or an interval counted in cycles
    due_at: datetime | None      # the UTC moment the reminder goes out
    last_done: date | None
    last_done_source: LastDoneSource | None
    reason: Reason
    quote: str | None            # the interval's verbatim quote, in the manual's language; None: the manual gives none
    quote_lang: str | None
    page: int | None
    duration_min: float | None   # only when the card gives a source for it
    lang: str
    every: int
    unit: str
    due_name: str                # the short noun a reminder names: "filter", "debris filter"
    after: tuple[AfterNote, ...] = ()
    schedule: Schedule | None = None     # the household's own; None: the manual's (every, unit above)
    window_end: date | None = None       # a window schedule's last + max: overdue after it
    snoozed_until: date | None = None    # a job snooze: the job is due on it instead
    status: Status = "none"
    assignee: str | None = None          # a person id

    @property
    def manual_cited(self) -> bool:
        """False for an upkeep job whose manual gives no interval: it is on /due by the household's schedule only."""
        return self.quote is not None

    @property
    def interval_quote(self) -> str | None:
        return f"from your manual, p. {self.page}" if self.manual_cited else None

    def is_due(self, now: datetime) -> bool:
        return self.due_at is not None and self.due_at <= now

    def api(self) -> dict[str, object]:
        """The contract's Due object (api.openapi.yaml)."""
        return {
            "appliance_id": self.appliance_id, "job_id": self.job_id, "title": self.title,
            **({"due_name": self.due_name} if self.due_name else {}),  # contracts 1.40: never null or ""
            "due_on": self.due_on.isoformat() if self.due_on else None,
            "last_done": {"date": self.last_done.isoformat() if self.last_done else None,
                          "source": self.last_done_source or "unknown"},
            "interval_quote": self.interval_quote, "page": self.page,
            "reason": self.reason, "quote": self.quote, "duration_min": self.duration_min,
            "manual": {"every": self.every, "unit": self.unit, "page": self.page, "quote": self.quote}
            if self.manual_cited else None,
            "schedule": self.schedule.api() if self.schedule else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "status": self.status,
            "snoozed_until": self.snoozed_until.isoformat() if self.snoozed_until else None,
            "assignee": self.assignee,
        }


def pick_lang(text: dict[str, str], lang: str) -> str:
    for k in (lang, "en"):
        if k in text:
            return text[k]
    return next(iter(text.values()))


def due_name(job: Job, lang: str) -> str:
    """The card's own short name for the job (Job.due_name, when the card has one), else one derived from the
    title: "Clean the debris filter" -> "debris filter", "Pompfilter schoonmaken" -> "pompfilter"."""
    if job.due_name is not None:
        return pick_lang(job.due_name.root, lang)
    title = pick_lang(job.title.root, lang).strip().rstrip(".")
    words = title.split()
    low = f" {title.lower()} "
    if " the " in low:                                    # en: the object after the article
        name = title[low.index(" the ") + 4:]
    elif len(words) > 1 and words[-1].lower().endswith("en"):  # nl: the object before the infinitive
        name = " ".join(words[:-1])
    elif len(words) > 1:                                  # en without an article: drop the verb
        name = " ".join(words[1:])
    else:
        name = title
    return name[:1].lower() + name[1:]


def checked(card: Card | None) -> bool:
    return card is not None and card.checks is not None and card.checks.citation.status == "pass"


def _quote(card: Card, ref: str | None) -> RefQuote | None:
    return next((q for q in card.quotes if q.id == ref), None)


def job_due(job: Job, card: Card, *, last_done: date | None, source: LastDoneSource | None,
            today: date) -> tuple[date | None, Reason] | None:
    """(due_on, reason) for one upkeep job by the manual's interval, or None if the job has no checked interval."""
    got = job_due_for(job, card, last_done=last_done, source=source, today=today)
    return None if got is None else (got[0], got[1])


def job_due_for(job: Job, card: Card, *, last_done: date | None, source: LastDoneSource | None, today: date,
                schedule: Schedule | None = None,
                snoozed_until: date | None = None) -> tuple[date | None, Reason, date | None, Status] | None:
    """(due_on, reason, window_end, status) for one upkeep job, by the household's schedule when it has one, else
    the manual's; None if the job has no checked interval and no schedule of the household's own (an upkeep job
    whose manual gives no interval is on /due only once a person set one). A snooze only ever moves due_on later."""
    if job.kind != "upkeep":
        return None
    iv = job.interval
    if iv is None:
        if schedule is None or not (schedule.every or (schedule.min and schedule.max)):
            return None
    elif _quote(card, iv.quote_ref) is None:
        return None
    elif iv.unit == "cycle" and schedule is None:
        return None, "cycle", None, "none"  # a cycle count has no date, until the household gives it a time
    if source is None:
        return None, "unanswered", None, "none"
    end: date | None = None
    reason: Reason
    if source in ("never", "unknown") or last_done is None:
        due_on: date | None = today
        reason = "never" if source == "never" else "unknown"
    else:
        reason = "scheduled"
        if schedule is not None and schedule.kind == "every" and schedule.every:
            due_on = add_interval(last_done, schedule.every, schedule.unit)
        elif schedule is not None and schedule.kind == "window" and schedule.min and schedule.max:
            due_on = add_interval(last_done, schedule.min, schedule.unit)
            end = add_interval(last_done, schedule.max, schedule.unit)
        else:
            assert iv is not None  # a job without the manual's interval has a complete schedule (above)
            due_on = add_interval(last_done, iv.every, iv.unit)
    if due_on is not None and snoozed_until is not None and snoozed_until > due_on:
        due_on, end = snoozed_until, None  # due on the snooze date; a window's end no longer applies
    if due_on is None:
        return None, reason, end, "none"
    if today < due_on:
        status: Status = "upcoming"
    elif today > (end or due_on):
        status = "overdue"
    else:
        status = "due"
    return due_on, reason, end, status


def after_notes(conn: sqlite3.Connection, household_id: str, card: Card, job: Job, *, since: date | None,
                today: date, lang: str) -> tuple[AfterNote, ...]:
    """The latest history entry of each Job.after job since this job's last_done, with the card's checked line."""
    notes = []
    for a in job.after or ():
        q = _quote(card, a.quote_ref)
        if q is None:
            continue
        row = conn.execute(
            "SELECT text, on_date FROM history WHERE household_id = ? AND job_id = ? AND on_date <= ?"
            " AND (? IS NULL OR on_date > ?) ORDER BY on_date DESC, id DESC LIMIT 1",
            (household_id, a.job_id, today.isoformat(), since and since.isoformat(), since and since.isoformat()),
        ).fetchone()
        if row is None:
            continue
        other = next((j for j in card.jobs if j.id == a.job_id), None)
        notes.append(AfterNote(
            job_id=a.job_id, job_title=pick_lang(other.title.root, lang) if other else a.job_id,
            history_text=(row["text"] or None), on=date.fromisoformat(row["on_date"]),
            because=pick_lang(a.because.root, lang), page=q.page))
    return tuple(notes)


def due(conn: sqlite3.Connection, household_id: str, now: datetime, load_card: CardLoader) -> list[Due]:
    hh = conn.execute("SELECT country, lang FROM households WHERE id = ? AND deleted_at IS NULL",
                      (household_id,)).fetchone()
    if hh is None:
        return []
    tz = household_tz(hh["country"])
    today = local_today(now, tz)
    rows = conn.execute(
        "SELECT a.id, a.alias, c.card_path, c.status FROM appliances a JOIN cards_ref c ON c.appliance_id = a.id"
        " WHERE a.household_id = ? AND a.deleted_at IS NULL ORDER BY a.created_at, a.id", (household_id,)).fetchall()
    out: list[Due] = []
    for r in rows:
        card = load_card(r["card_path"])
        if card is None or not checked(card):
            continue
        states = {s["job_id"]: s for s in conn.execute(
            "SELECT job_id, last_done, last_done_source FROM job_state WHERE appliance_id = ?", (r["id"],))}
        own = {s["job_id"]: s for s in conn.execute("SELECT * FROM job_schedule WHERE appliance_id = ?", (r["id"],))}
        for job in card.jobs:
            st = states.get(job.id)
            last = date.fromisoformat(st["last_done"]) if st and st["last_done"] else None
            source: LastDoneSource | None = st["last_done_source"] if st else None
            sched, snoozed = schedule_of(own.get(job.id))
            got = job_due_for(job, card, last_done=last, source=source, today=today, schedule=sched,
                              snoozed_until=snoozed)
            if got is None:
                continue
            due_on, reason, window_end, status = got
            q = _quote(card, job.interval.quote_ref) if job.interval else None  # None: the household's schedule only
            if st is not None:
                conn.execute("UPDATE job_state SET due_on = ? WHERE appliance_id = ? AND job_id = ?",
                             (due_on.isoformat() if due_on else None, r["id"], job.id))
            out.append(Due(
                household_id=household_id, appliance_id=r["id"], alias=r["alias"], job_id=job.id,
                title=pick_lang(job.title.root, hh["lang"]), due_on=due_on,
                due_at=reminder_at(due_on, tz) if due_on else None, last_done=last, last_done_source=source,
                reason=reason, quote=q.text if q else None, quote_lang=q.lang if q else None,
                page=q.page if q else None,
                duration_min=job.duration_min.value if job.duration_min else None, lang=hh["lang"],
                every=job.interval.every if job.interval else 0, unit=job.interval.unit if job.interval else "",
                due_name=due_name(job, hh["lang"]),
                after=after_notes(conn, household_id, card, job, since=last, today=today, lang=hh["lang"]),
                schedule=sched, window_end=window_end, snoozed_until=snoozed, status=status,
                assignee=own[job.id]["assignee"] if job.id in own else None,
            ))
    return out


def schedule_of(row: sqlite3.Row | None) -> tuple[Schedule | None, date | None]:
    """A job_schedule row as (the household's schedule, the snooze date)."""
    if row is None:
        return None, None
    snoozed = date.fromisoformat(row["snoozed_until"]) if row["snoozed_until"] else None
    if row["kind"] is None:
        return None, snoozed
    return Schedule(row["kind"], row["unit"], row["every"], row["min"], row["max"]), snoozed


def yaml_card_loader(root: Path) -> CardLoader:
    """A loader for tests and tools: reads the YAML and keeps whatever `checks` it holds. On disk a card has no
    checks, so this fails closed (no reminders) until the real card loader fills them."""
    import yaml

    def load(path: str) -> Card | None:
        p = Path(path) if Path(path).is_absolute() else root / path
        try:
            return Card.model_validate(yaml.safe_load(p.read_text()))
        except (OSError, ValueError):
            return None

    return load
