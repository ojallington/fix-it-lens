"""Fix-It Lens: the household's upkeep schedule in Home Assistant. The home is a service device with a to-do list and
a calendar; each appliance is a device under it (maker and model from its card) with a next-due date sensor per job,
its next job and how many are overdue. An event fires when a job falls due, and two services: mark_done and snooze.

It reads the Fix-It Lens REST API with a token the household made for its Home Assistant. It controls no device and
calls no Home Assistant service; the only writes are a job done or snoozed, sent to Fix-It Lens when a person asks.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import date, timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import CONF_TOKEN, CONF_URL, DEFAULT_TITLE, DOMAIN, SERVICE_MARK_DONE, SERVICE_SNOOZE
from .coordinator import FixitCoordinator
from .core import AuthFailed, Client, Job, Refused, Unreachable
from .entity import home_device

PLATFORMS = [Platform.SENSOR, Platform.TODO, Platform.CALENDAR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type FixitConfigEntry = ConfigEntry[FixitCoordinator]

JOB = vol.Match(r"^[^:\s]+:[^:\s]+$")  # "<appliance_id>:<job_id>", as the event and the sensors' `job` give it
MARK_DONE = vol.Schema({vol.Required("job"): JOB, vol.Optional("date"): cv.date})
SNOOZE = vol.All(vol.Schema({vol.Required("job"): JOB, vol.Exclusive("until", "when"): cv.date,
                             vol.Exclusive("days", "when"): vol.All(int, vol.Range(min=1, max=365))}),
                 cv.has_at_least_one_key("until", "days"))


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def mark_done(call: ServiceCall) -> None:
        coordinator, job = _find(hass, call.data["job"])
        on: date = call.data.get("date") or dt_util.now().date()
        await _send(coordinator, coordinator.client.mark_done(job, on))

    async def snooze(call: ServiceCall) -> None:
        coordinator, job = _find(hass, call.data["job"])
        until: date | None = call.data.get("until")
        if until is None:  # `days` later than the date it is due now, or than today if that is later
            until = max(dt_util.now().date(), job.due_on or date.min) + timedelta(days=call.data["days"])
        await _send(coordinator, coordinator.client.snooze(job, until))

    hass.services.async_register(DOMAIN, SERVICE_MARK_DONE, mark_done, schema=MARK_DONE)
    hass.services.async_register(DOMAIN, SERVICE_SNOOZE, snooze, schema=SNOOZE)
    return True


def _find(hass: HomeAssistant, key: str) -> tuple[FixitCoordinator, Job]:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            coordinator: FixitCoordinator = entry.runtime_data
            for j in coordinator.data.jobs if coordinator.data else []:
                if j.key == key:
                    return coordinator, j
    raise ServiceValidationError(f"No Fix-It Lens job {key}: use the `job` attribute of its next-due sensor.")


async def _send(coordinator: FixitCoordinator, change: Awaitable[None]) -> None:
    try:
        await change
    except Refused as e:
        raise ServiceValidationError(str(e)) from e
    except (AuthFailed, Unreachable) as e:
        raise HomeAssistantError(f"Fix-It Lens: {e}") from e
    await coordinator.async_request_refresh()


async def async_setup_entry(hass: HomeAssistant, entry: FixitConfigEntry) -> bool:
    client = Client(entry.data[CONF_URL], entry.data[CONF_TOKEN], http=get_async_client(hass))
    coordinator = FixitCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    if entry.title == DEFAULT_TITLE and coordinator.data.home:
        # an entry made before entries were named after their home
        hass.config_entries.async_update_entry(entry, title=coordinator.data.home)
    # The home's device first: each appliance's device hangs under it (via_device).
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **home_device(coordinator))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def prune() -> None:
        """An appliance the home no longer has loses its device (and its entities with it)."""
        if coordinator.data is None:
            return
        keep = {(DOMAIN, entry.entry_id)} | {(DOMAIN, f"{entry.entry_id}:{aid}")
                                            for aid in coordinator.data.appliance_ids()}
        reg = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(reg, entry.entry_id):
            if not device.identifiers & keep:
                reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    prune()
    entry.async_on_unload(coordinator.async_add_listener(prune))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FixitConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
