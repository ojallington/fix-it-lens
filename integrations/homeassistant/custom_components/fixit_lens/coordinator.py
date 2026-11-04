"""Polls GET /api/v1/due every minute, so a job done or snoozed in the app shows here within a minute, and the
household and its appliances every 30 minutes, or at once when /due names an appliance not seen yet. A refused token
asks the user to re-authenticate.

When a job becomes due or overdue it fires `fixit_lens_job_due` (Job.event()) once for that due date and status,
remembered across restarts, so an automation can send a notification with a Done button (blueprints/).
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_JOB_DUE, HOME_INTERVAL, SCAN_INTERVAL
from .core import AuthFailed, Client, Job, Snapshot, Unreachable

_LOGGER = logging.getLogger(__name__)


class FixitCoordinator(DataUpdateCoordinator[Snapshot]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Client) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.client = client
        self._store: Store[dict[str, str]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.announced")
        self._announced: dict[str, str] | None = None  # job key -> "due_on:status" last fired
        self._home_at: datetime | None = None  # when the household and its appliances were last read

    async def _async_update_data(self) -> Snapshot:
        try:
            snap = await self._poll()
        except AuthFailed as e:
            raise ConfigEntryAuthFailed(str(e)) from e
        except Unreachable as e:
            raise UpdateFailed(str(e)) from e
        await self._announce(snap.jobs)
        return snap

    async def _poll(self) -> Snapshot:
        old = self.data
        if old is not None and self._home_at is not None and dt_util.utcnow() - self._home_at < HOME_INTERVAL:
            jobs = await self.client.jobs(list(old.appliances.values()))
            if all(j.appliance_id in old.appliances for j in jobs):
                return Snapshot(old.home, old.appliances, jobs)
        snap = await self.client.snapshot()
        self._home_at = dt_util.utcnow()
        return snap

    async def async_reread_home(self) -> None:
        """Read the household and its appliances now, not at the next half hour: after an add from here."""
        self._home_at = None
        await self.async_refresh()

    async def _announce(self, jobs: list[Job]) -> None:
        if self._announced is None:
            self._announced = await self._store.async_load() or {}
        now = {j.key: f"{j.due_on}:{j.status}" for j in jobs if j.status in ("due", "overdue")}
        for j in jobs:
            if j.key in now and self._announced.get(j.key) != now[j.key]:
                self.hass.bus.async_fire(EVENT_JOB_DUE, j.event())
        if now != self._announced:
            self._announced = now
            await self._store.async_save(now)
