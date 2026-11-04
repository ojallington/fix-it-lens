"""Calendar arithmetic for due dates. Due dates are calendar dates; only the reminder's moment has a time zone."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

Unit = Literal["day", "week", "month", "year", "cycle"]

# The hour, local to the household, at which a due reminder goes out.
REMINDER_HOUR = 9

COUNTRY_TZ = {"nl": "Europe/Amsterdam", "gb": "Europe/London", "uk": "Europe/London", "be": "Europe/Brussels",
              "de": "Europe/Berlin", "fr": "Europe/Paris", "ie": "Europe/Dublin"}


def add_months(d: date, months: int) -> date:
    """d plus whole months; a day past the target month's end lands on its last day (31 Jan + 1 month = 28/29 Feb)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def add_interval(d: date, every: int, unit: Unit) -> date | None:
    """The next due date after `d`; None for a cycle count, which no calendar can compute."""
    if every < 1:
        raise ValueError("every must be at least 1")
    match unit:
        case "day":
            return d + timedelta(days=every)
        case "week":
            return d + timedelta(weeks=every)
        case "month":
            return add_months(d, every)
        case "year":
            return add_months(d, 12 * every)
        case "cycle":
            return None


def household_tz(country: str) -> ZoneInfo:
    return ZoneInfo(COUNTRY_TZ.get(country.lower(), "Europe/Amsterdam"))


def reminder_at(due_on: date, tz: ZoneInfo, hour: int = REMINDER_HOUR) -> datetime:
    """The UTC moment a reminder for `due_on` goes out: `hour` o'clock on that day, wall time in `tz`."""
    return datetime.combine(due_on, time(hour), tzinfo=tz).astimezone(UTC)


def local_today(now: datetime, tz: ZoneInfo) -> date:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(tz).date()
