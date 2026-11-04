from datetime import UTC, date, datetime

import pytest

from app.schedule.dates import add_interval, add_months, household_tz, local_today, reminder_at


@pytest.mark.parametrize(("start", "months", "want"), [
    (date(2026, 1, 31), 1, date(2026, 2, 28)),
    (date(2028, 1, 31), 1, date(2028, 2, 29)),
    (date(2026, 8, 31), 3, date(2026, 11, 30)),
    (date(2026, 11, 30), 3, date(2027, 2, 28)),
    (date(2026, 12, 15), 1, date(2027, 1, 15)),
    (date(2026, 10, 27), 24, date(2028, 10, 27)),
])
def test_month_ends(start: date, months: int, want: date) -> None:
    assert add_months(start, months) == want


def test_units() -> None:
    d = date(2026, 10, 27)
    assert add_interval(d, 10, "day") == date(2026, 11, 6)
    assert add_interval(d, 2, "week") == date(2026, 11, 10)
    assert add_interval(date(2028, 2, 29), 1, "year") == date(2029, 2, 28)
    assert add_interval(d, 30, "cycle") is None
    with pytest.raises(ValueError):
        add_interval(d, 0, "day")


def test_reminder_hour_holds_across_dst() -> None:
    tz = household_tz("nl")
    # The clocks go back on 25 Oct 2026 and forward on 28 Mar 2027: 09:00 local each side.
    assert reminder_at(date(2026, 10, 24), tz) == datetime(2026, 10, 24, 7, tzinfo=UTC)
    assert reminder_at(date(2026, 10, 26), tz) == datetime(2026, 10, 26, 8, tzinfo=UTC)
    assert reminder_at(date(2027, 3, 29), tz) == datetime(2027, 3, 29, 7, tzinfo=UTC)
    assert reminder_at(date(2026, 10, 25), household_tz("gb")) == datetime(2026, 10, 25, 9, tzinfo=UTC)


def test_local_today_near_midnight() -> None:
    tz = household_tz("nl")
    assert local_today(datetime(2026, 10, 27, 23, 30, tzinfo=UTC), tz) == date(2026, 10, 28)
    with pytest.raises(ValueError):
        local_today(datetime(2026, 10, 27), tz)
