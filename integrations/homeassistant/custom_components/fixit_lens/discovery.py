"""Discovery: the appliances Home Assistant already knows (Home Connect's dishwasher, a Miele washer), offered as a
list to tick, at setup and under Configure. Nothing is ticked at first and nothing leaves Home Assistant until the
user submits; then only each ticked one's maker, model, kind and name go to Fix-It Lens, through its usual add. A model
the card library has links its card at once; any other gets its manual's pages, and an add from here builds no card.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .core import Appliance, Client, Found, ReadOnly, Refused, Unreachable, discovered, offer

CONF_DEVICES = "devices"


def known(hass: HomeAssistant, home: list[Appliance]) -> list[Found]:
    """The devices in Home Assistant's registry that look like appliances the home does not have yet."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    ours = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    found: list[Found] = []
    for d in devices.devices.values():
        if d.disabled or d.entry_type is dr.DeviceEntryType.SERVICE or ours & d.config_entries:
            continue
        entry = hass.config_entries.async_get_entry(d.primary_config_entry or next(iter(d.config_entries), ""))
        domains = {e.domain for e in er.async_entries_for_device(entities, d.id)}
        f = discovered(d.id, manufacturer=d.manufacturer, model=d.model, name=d.name_by_user or d.name,
                       model_id=d.model_id, source=entry.domain if entry else None,
                       only_lights=bool(domains) and domains <= {"light"})
        if f is not None:
            found.append(f)
    return offer(sorted(found, key=lambda f: f.label.lower()), home)


def schema(found: list[Found]) -> vol.Schema:
    return vol.Schema({vol.Optional(CONF_DEVICES, default=[]): cv.multi_select({f.device_id: f.label
                                                                                for f in found})})


async def add_ticked(client: Client, found: list[Found], ticked: list[str]) -> tuple[list[str], dict[str, str],
                                                                                      dict[str, Any]]:
    """Add the ticked ones, one by one: the device ids added, and the form's errors and their placeholders."""
    added: list[str] = []
    refused: list[str] = []
    by_id = {f.device_id: f for f in found}
    for device_id in ticked:
        f = by_id.get(device_id)
        if f is None:
            continue
        try:
            await client.add(f)
        except ReadOnly:
            return added, {"base": "read_only"}, {}
        except Refused as e:
            refused.append(f"{f.alias} ({e})")
            continue
        except Unreachable:
            return added, {"base": "cannot_connect"}, {}
        added.append(device_id)
    if refused:
        return added, {"base": "refused"}, {"refused": "; ".join(refused)}
    return added, {}, {}
