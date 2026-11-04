"""Settings → Devices & services → Fix-It Lens → Download diagnostics: the entry and what the last poll read. The
token and the Fix-It Lens address (also inside each job's link) are redacted. `dashboard` is a ready dashboard for this
home's appliances, from Home Assistant's own cards: paste it into a new dashboard's raw configuration editor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FixitConfigEntry
from .const import CONF_TOKEN, CONF_URL
from .core import dashboard

REDACT = {CONF_TOKEN, CONF_URL, "link"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FixitConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    snap = coordinator.data
    return {
        "entry": async_redact_data({"title": entry.title, "data": dict(entry.data)}, REDACT),
        "last_update_success": coordinator.last_update_success,
        "home": snap.home,
        "appliances": [{"id": a.id, "alias": a.alias, "kind": a.kind, "brand": a.brand, "model": a.model}
                       for a in snap.appliances.values()],
        "dashboard": dashboard([snap.appliance(aid).alias for aid in snap.appliance_ids()], entry.title),
        "jobs": async_redact_data([{**j.attributes(), "due_on": j.due_on.isoformat() if j.due_on else None,
                                    "name": j.name} for j in snap.jobs], REDACT),
    }
