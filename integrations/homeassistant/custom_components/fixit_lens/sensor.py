"""Sensors on each appliance's device: when each upkeep job is next due (a date; sensor.<item>_next_due), the next job
by name, and how many jobs are due now and how many overdue."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FixitConfigEntry
from .coordinator import FixitCoordinator
from .core import Job, slug
from .entity import ApplianceEntity


async def async_setup_entry(hass: HomeAssistant, entry: FixitConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        snap = coordinator.data
        new: list[SensorEntity] = []
        for aid in snap.appliance_ids():
            if aid not in known:
                known.add(aid)
                new += [NextJob(coordinator, aid), Overdue(coordinator, aid), DueNow(coordinator, aid)]
        for j in snap.jobs:
            if j.key not in known:
                known.add(j.key)
                new.append(NextDue(coordinator, j))
        async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class NextDue(ApplianceEntity, SensorEntity):
    """One job's next due date: "Debris filter due"."""

    _attr_device_class = SensorDeviceClass.DATE
    _attr_translation_key = "job_due"

    def __init__(self, coordinator: FixitCoordinator, job: Job) -> None:
        super().__init__(coordinator, job.appliance_id)
        self._key = job.key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:{job.key}:next_due"
        self._attr_translation_placeholders = {"job": job.name}
        self.entity_id = f"sensor.{job.object_id}"

    @property
    def _job(self) -> Job | None:
        return next((j for j in self.coordinator.data.jobs if j.key == self._key), None)

    @property
    def available(self) -> bool:
        return super().available and self._job is not None

    @property
    def native_value(self) -> date | None:
        job = self._job
        return job.due_on if job else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        job = self._job
        return job.attributes() if job else {}


class NextJob(ApplianceEntity, SensorEntity):
    """The appliance's soonest dated job, by name, with its date and status."""

    _attr_translation_key = "next_job"

    def __init__(self, coordinator: FixitCoordinator, appliance_id: str) -> None:
        super().__init__(coordinator, appliance_id)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:{appliance_id}:next_job"
        self.entity_id = f"sensor.{slug(coordinator.data.appliance(appliance_id).alias)}_next_job"

    @property
    def _next(self) -> Job | None:
        dated = [j for j in self.coordinator.data.jobs if j.appliance_id == self.appliance_id and j.due_on]
        return min(dated, key=lambda j: (j.due_on or date.max, j.key)) if dated else None

    @property
    def native_value(self) -> str | None:
        job = self._next
        return job.name if job else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        job = self._next
        if job is None:
            return {}
        return {"due_on": job.due_on.isoformat() if job.due_on else None, "status": job.status, "job": job.key,
                "title": job.title, "link": job.link}


class Overdue(ApplianceEntity, SensorEntity):
    """How many of the appliance's jobs are past their date (or past the end of the household's window)."""

    _attr_translation_key = "overdue"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _status = "overdue"
    _suffix = "overdue_jobs"

    def __init__(self, coordinator: FixitCoordinator, appliance_id: str) -> None:
        super().__init__(coordinator, appliance_id)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}:{appliance_id}:{self._status}"
        self.entity_id = f"sensor.{slug(coordinator.data.appliance(appliance_id).alias)}_{self._suffix}"

    @property
    def _these(self) -> list[Job]:
        return [j for j in self.coordinator.data.jobs if j.appliance_id == self.appliance_id
                and j.status == self._status]

    @property
    def native_value(self) -> int:
        return len(self._these)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"jobs": [j.name for j in self._these]}


class DueNow(Overdue):
    """How many of the appliance's jobs are due now: on or past their date, or inside the household's window, and
    not yet overdue."""

    _attr_translation_key = "due_now"
    _status = "due"
    _suffix = "due_jobs"
