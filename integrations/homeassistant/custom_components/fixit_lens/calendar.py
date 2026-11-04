"""A calendar with one all-day event per job from its due date: one day, or the household's whole window (the same
dates as the .ics feed)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FixitConfigEntry
from .core import Job
from .entity import FixitEntity


async def async_setup_entry(hass: HomeAssistant, entry: FixitConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([UpkeepCalendar(entry.runtime_data)])


def _event(j: Job, on: date) -> CalendarEvent:
    last = j.window_end if j.window_end and j.window_end > on else on
    return CalendarEvent(start=on, end=last + timedelta(days=1), summary=j.short_summary,
                         description=f"{j.title}\n{j.description()}", uid=f"{j.key}:{on.isoformat()}")


class UpkeepCalendar(FixitEntity, CalendarEntity):
    _attr_translation_key = "upkeep"

    def __init__(self, coordinator) -> None:  # type: ignore[no-untyped-def]
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:calendar"
        self.entity_id = "calendar.fix_it_lens_upkeep"

    @property
    def event(self) -> CalendarEvent | None:
        dated = [j for j in self.coordinator.data.jobs if j.due_on is not None]
        if not dated:
            return None
        j = min(dated, key=lambda j: j.due_on or date.max)
        return _event(j, j.due_on) if j.due_on else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime,
                               end_date: datetime) -> list[CalendarEvent]:
        return [e for j in self.coordinator.data.jobs if j.due_on is not None and (e := _event(j, j.due_on))
                and e.start < end_date.date() + timedelta(days=1) and e.end > start_date.date()]
