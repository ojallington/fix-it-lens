"""The words a due job is shown in, in the storyboard's forms. Every name, duration, interval and page comes from the
checked card (the job, its sourced duration, its interval and the interval quote's page), or from the schedule a
person in the household set for a job whose manual gives no interval; none is written here.

- the push, `reminder`: "Your dishwasher's filter is due. 10 minutes. Start?" + "from your manual, p. 43";
- the schedule line after a lookup, `schedule_line`: "Washer: debris filter due · every 2 months · manual p. 52 ·
  last done: never.";
- after a checked job, `next_line`: "Washer filter: next due in 8 weeks. I'll remind you."

One reminder per job per due date, per channel; nothing here changes a due date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

from app.schedule.due import AfterNote, Due
from app.schedule.store import reminder_key

MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          "nl": ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]}

UNITS = {
    "en": {"day": ("day", "days"), "week": ("week", "weeks"), "month": ("month", "months"), "year": ("year", "years")},
    "nl": {"day": ("dag", "dagen"), "week": ("week", "weken"), "month": ("maand", "maanden"),
           "year": ("jaar", "jaar")},
}

TEXT = {
    "en": {
        "title": "Your {alias}'s {name} is due.",
        "overdue": "Due since {since}.",
        "never": "You said it has never been done.",
        "unknown": "You weren't sure when it was last done.",
        "minutes": "{n} minutes.",
        "start": "Start?",
        "page": "from your manual, p. {page}",
        "own": "your schedule · the manual gives no interval",
        "line": "{Alias}: {name} due · every {every} · manual p. {page} · last done: {last}.",
        "own_line": "{Alias}: {name} due · {yours} · last done: {last}.",
        "why": "Last done: {last}. Your manual says every {every} (p. {page}).",
        "own_why": "Last done: {last}. {yours} The manual gives no interval.",
        "yours_every": "You chose every {every}.",
        "yours_window": "You chose every {min} to {max} {unit}.",
        "never_short": "never",
        "unknown_short": "don't know",
        "next": "{Alias} {name}: next due in {n} {unit}. I'll remind you.",
        "head": "{Alias}: {title}.",
        "you": "You {text} {when}.",
        "done": "{Title}: done {when}.",
        "because": "{because} ({alias} guide p. {page}).",
        "today": "today", "yesterday": "yesterday", "on_day": "on {day}", "on_date": "on {date}",
        "weekdays": "Monday Tuesday Wednesday Thursday Friday Saturday Sunday",
    },
    "nl": {
        "title": "De {name} van je {alias} is aan de beurt.",
        "overdue": "Aan de beurt sinds {since}.",
        "never": "Je zei dat het nog nooit is gedaan.",
        "unknown": "Je wist niet meer wanneer het voor het laatst is gedaan.",
        "minutes": "{n} minuten.",
        "start": "Beginnen?",
        "page": "uit je handleiding, p. {page}",
        "own": "jouw schema · de handleiding geeft geen interval",
        "line": "{Alias}: {name} aan de beurt · elke {every} · handleiding p. {page} · laatst gedaan: {last}.",
        "own_line": "{Alias}: {name} aan de beurt · {yours} · laatst gedaan: {last}.",
        "why": "Laatst gedaan: {last}. Je handleiding zegt elke {every} (p. {page}).",
        "own_why": "Laatst gedaan: {last}. {yours} De handleiding geeft geen interval.",
        "yours_every": "Jij koos elke {every}.",
        "yours_window": "Jij koos elke {min} tot {max} {unit}.",
        "never_short": "nooit",
        "unknown_short": "weet ik niet",
        "next": "{Alias} {name}: weer aan de beurt over {n} {unit}. Ik herinner je eraan.",
        "head": "{Alias}: {title}.",
        "you": "Je {text} {when}.",
        "done": "{Title}: gedaan {when}.",
        "because": "{because} (handleiding {alias}, p. {page}).",
        "today": "vandaag", "yesterday": "gisteren", "on_day": "op {day}", "on_date": "op {date}",
        "weekdays": "maandag dinsdag woensdag donderdag vrijdag zaterdag zondag",
    },
}


def _t(lang: str) -> dict[str, str]:
    return TEXT.get(lang, TEXT["en"])


def _day(d: date, lang: str) -> str:
    return f"{d.day} {MONTHS.get(lang, MONTHS['en'])[d.month - 1]}"


def _unit(lang: str, unit: str, n: int) -> str:
    one, many = UNITS.get(lang, UNITS["en"])[unit]
    return one if n == 1 else many


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _grammar(s: str) -> str:
    """Dutch says "elk jaar" (a neuter noun), "elke maand"."""
    return s.replace("elke jaar", "elk jaar").replace("Elke jaar", "Elk jaar")


@dataclass(frozen=True)
class Reminder:
    key: str
    household_id: str
    appliance_id: str
    job_id: str
    title: str
    body: str
    footnote: str      # the grey line: "from your manual, p. 43", or "your schedule · the manual gives no interval"
    page: int | None   # None: the manual gives no interval, so nothing is cited
    url: str           # opens the job in the PWA, with what its landing shows (alias, why, minutes)
    alias: str = ""
    why: str = ""      # "Last done: 3 Sep. Your manual says every month (p. 43)."
    minutes: int | None = None

    def payload(self) -> dict[str, object]:
        return {"key": self.key, "title": self.title, "body": f"{self.body}\n{self.footnote}", "body_main": self.body,
                "footnote": self.footnote, "page": self.page, "url": self.url,
                "appliance_id": self.appliance_id, "job_id": self.job_id, "alias": self.alias, "why": self.why,
                "minutes": self.minutes}


def reminder(d: Due, today: date) -> Reminder:
    if d.due_on is None:
        raise ValueError("an unanswered or cycle-counted job has no reminder")
    t = _t(d.lang)
    parts = [after_clause(n, d, today) for n in d.after]
    if d.reason == "never":
        parts.append(t["never"])
    elif d.reason == "unknown":
        parts.append(t["unknown"])
    elif d.due_on < today:
        parts.append(t["overdue"].format(since=_day(d.due_on, d.lang)))
    if d.duration_min is not None:
        parts.append(t["minutes"].format(n=round(d.duration_min)))
    parts.append(t["start"])
    key = reminder_key(d.appliance_id, d.job_id, d.due_on)
    why = why_line(d)
    minutes = round(d.duration_min) if d.duration_min is not None else None
    q = {"appliance": d.appliance_id, "job": d.job_id, "reminder": key, "alias": d.alias, "why": why}
    if minutes is not None:
        q["min"] = str(minutes)
    return Reminder(
        key=key, household_id=d.household_id, appliance_id=d.appliance_id, job_id=d.job_id,
        title=t["title"].format(alias=d.alias, name=d.due_name), body=" ".join(parts),
        footnote=t["page"].format(page=d.page) if d.manual_cited else t["own"], page=d.page, url="/?" + urlencode(q),
        alias=d.alias, why=why, minutes=minutes,
    )


def _every(d: Due) -> str:
    return f"{d.every} {_unit(d.lang, d.unit, d.every)}" if d.every > 1 else _unit(d.lang, d.unit, 1)


def _last(d: Due) -> str:
    t = _t(d.lang)
    if d.last_done is not None:
        return _day(d.last_done, d.lang)
    return t["never_short"] if d.last_done_source == "never" else t["unknown_short"]


def _yours(d: Due) -> str:
    """The household's own schedule as a sentence, "You chose every 3 to 6 months.", or "" when it set none."""
    t, s = _t(d.lang), d.schedule
    if s is not None and s.kind == "every" and s.every:
        n = s.every
        every = f"{n} {_unit(d.lang, s.unit, n)}" if n > 1 else _unit(d.lang, s.unit, 1)
        return t["yours_every"].format(every=every)
    if s is not None and s.kind == "window" and s.min and s.max:
        return t["yours_window"].format(min=s.min, max=s.max, unit=_unit(d.lang, s.unit, s.max))
    return ""


def why_line(d: Due) -> str:
    """Why a reminder came, for its landing: "Last done: 3 Sep. Your manual says every month (p. 43).", and the
    household's own schedule after it when it set one: "You chose every 3 to 6 months.". For a job whose manual
    gives no interval: "Last done: 3 Sep. You chose every year. The manual gives no interval."."""
    t = _t(d.lang)
    if not d.manual_cited:
        return _grammar(t["own_why"].format(last=_last(d), yours=_yours(d)))
    line = t["why"].format(last=_last(d), every=_every(d), page=d.page)
    yours = _yours(d)
    return _grammar(f"{line} {yours}" if yours else line)


def schedule_line(d: Due) -> str:
    """The line shown once the manual is found: what is due, how often, the page, and when it was last done."""
    t = _t(d.lang)
    if not d.manual_cited:
        yours = _yours(d).rstrip(".")
        return _grammar(t["own_line"].format(Alias=_cap(d.alias), name=d.due_name,
                                             yours=yours[:1].lower() + yours[1:], last=_last(d)))
    return _grammar(t["line"].format(Alias=_cap(d.alias), name=d.due_name, every=_every(d), page=d.page,
                                     last=_last(d)))


def next_line(d: Due, today: date) -> str:
    """After a checked job: when it is next due. Days under two weeks, else whole weeks (rounded down)."""
    if d.due_on is None:
        raise ValueError("no next date")
    days = max((d.due_on - today).days, 0)
    n, unit = (days, "day") if days < 14 else (days // 7, "week")
    return _t(d.lang)["next"].format(Alias=_cap(d.alias), name=d.due_name, n=n, unit=_unit(d.lang, unit, n))


def _when(on: date, today: date, lang: str) -> str:
    t = _t(lang)
    days = (today - on).days
    if days == 0:
        return t["today"]
    if days == 1:
        return t["yesterday"]
    if 1 < days < 7:
        return t["on_day"].format(day=t["weekdays"].split()[on.weekday()])
    return t["on_date"].format(date=_day(on, lang))


def after_clause(n: AfterNote, d: Due, today: date) -> str:
    """"You bled two radiators on Saturday. Bleeding drops the pressure (boiler guide p. 6)." The first sentence is
    the history row; the second is the card's own line, cited by the page of its checked quote."""
    t = _t(d.lang)
    when = _when(n.on, today, d.lang)
    if n.history_text:
        text = n.history_text.strip().rstrip(".")
        for lead in ("you ", "i ", "je ", "ik "):
            if text.lower().startswith(lead):
                text = text[len(lead):]
        said = t["you"].format(text=text[:1].lower() + text[1:], when=when)
    else:
        said = t["done"].format(Title=_cap(n.job_title), when=when)
    return f"{said} {t['because'].format(because=n.because.rstrip('.'), alias=d.alias, page=n.page)}"


def history_line(d: Due, today: date) -> str:
    """The storyboard's boiler line: "Boiler: check the pressure. You bled two radiators on Saturday. Bleeding drops the
    pressure (boiler guide p. 6)." Without a history entry it is the head alone."""
    t = _t(d.lang)
    head = t["head"].format(Alias=_cap(d.alias), title=d.title[:1].lower() + d.title[1:].rstrip("."))
    return " ".join([head, *(after_clause(n, d, today) for n in d.after)])
