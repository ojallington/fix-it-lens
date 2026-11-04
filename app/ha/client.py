"""A read-only client of the house's Home Assistant (it reads appliances and never controls one).

It makes GET requests to /api/ and /api/states only; it has no method that could call a service or write a state,
and tests fail if one appears. The token is read by path (~/.config/fleet/ha.token, or FIXIT_HA_TOKEN_FILE); the
address is FIXIT_HA_URL.

What it reads, for the agent's tools:
- `entities(alias)`: the appliance's entities, for `ha_state`;
- `power_w(alias)`: the power sensor in W, the second witness for plug out (None: no usable W reading);
- `power_state(alias)`: a binary power sensor, where there is no W sensor;
- `filter_reminder(alias)`, `operation_state(alias)`: what may start a job (the Bosch filter-reminder event
  sensors and the operation state). It never reads or claims an error code; the camera reads the display.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

DEFAULT_TOKEN = Path.home() / ".config/fleet/ha.token"
DEFAULT_URL = "http://homeassistant.local:8123"
TIMEOUT_S = 3.0


def configured() -> bool:
    """A token is in place (by path): Home Assistant is set up here. Without one, HA is simply not used."""
    return Path(os.environ.get("FIXIT_HA_TOKEN_FILE", DEFAULT_TOKEN)).is_file()

NO_READING = {"unavailable", "unknown", "", None}
# Home Connect's event sensors (enum confirmed / present / off) that say the filter wants cleaning.
FILTER_REMINDER_SUFFIXES = (
    "smart_filter_cleaning_reminder",
    "machine_care_and_filter_cleaning_reminder",
    "machine_care_and_low_maintenance_filter_cleaning_reminder",
)

PowerState = Literal["on", "off", "unknown"]
FilterReminder = Literal["confirmed", "present", "off"]


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    """Refuses anything but a GET under /api/, before it reaches the network."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method != "GET" or not (path == "/api/" or path.startswith("/api/states")):
            raise PermissionError(f"app/ha is read-only: refused {request.method} {path}")
        return await self.inner.handle_async_request(request)


@dataclass(frozen=True)
class Entity:
    entity_id: str
    state: str
    unit: str | None
    device_class: str | None
    last_changed: str | None

    def json(self) -> dict[str, Any]:
        return {"entity_id": self.entity_id, "state": self.state, "unit": self.unit,
                "last_changed": self.last_changed}


def _entity(raw: dict[str, Any]) -> Entity:
    attrs = raw.get("attributes") or {}
    return Entity(str(raw["entity_id"]), str(raw.get("state")), attrs.get("unit_of_measurement"),
                  attrs.get("device_class"), raw.get("last_changed"))


# Maps an appliance alias to its entity ids (from appliances.ha_entities, or a prefix such as
# "sensor.kitchen_dishwasher"); an alias with none has no Home Assistant.
EntityMap = Callable[[str], list[str]]


class HA:
    def __init__(self, entity_map: EntityMap, *, base_url: str | None = None, token_path: Path | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.entity_map = entity_map
        self.base_url = (base_url or os.environ.get("FIXIT_HA_URL") or DEFAULT_URL).rstrip("/")
        self.token_path = token_path or Path(os.environ.get("FIXIT_HA_TOKEN_FILE", DEFAULT_TOKEN))
        self._transport = ReadOnlyTransport(transport)

    def _client(self) -> httpx.AsyncClient:
        token = self.token_path.read_text().strip()
        return httpx.AsyncClient(base_url=self.base_url, transport=self._transport, timeout=TIMEOUT_S,
                                 headers={"Authorization": f"Bearer {token}"})

    async def _states(self) -> list[dict[str, Any]] | None:
        try:
            async with self._client() as c:
                r = await c.get("/api/states")
                r.raise_for_status()
                data: list[dict[str, Any]] = r.json()
                return data
        except (httpx.HTTPError, OSError, ValueError):
            return None

    async def available(self) -> bool:
        try:
            async with self._client() as c:
                return (await c.get("/api/")).status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    def _matches(self, alias: str, entity_id: str) -> bool:
        """An entry is a full entity id, or a prefix of the object id ("kitchen_dishwasher" matches
        sensor.kitchen_dishwasher_door)."""
        obj = entity_id.split(".", 1)[-1]
        return any(entity_id == m or ("." not in m and (obj == m or obj.startswith(m + "_")))
                   for m in self.entity_map(alias))

    async def entities(self, alias: str) -> tuple[bool, list[dict[str, Any]]]:
        """(Home Assistant answered, the alias's entities). (False, []) when HA is down or not set up."""
        if not self.entity_map(alias):
            return False, []
        states = await self._states()
        if states is None:
            return False, []
        return True, [_entity(s).json() for s in states if self._matches(alias, str(s.get("entity_id")))]

    async def _mine(self, alias: str) -> list[Entity]:
        if not self.entity_map(alias):
            return []
        states = await self._states() or []
        return [_entity(s) for s in states if self._matches(alias, str(s.get("entity_id")))]

    async def power_w(self, alias: str) -> float | None:
        """The power sensor's reading in W; None if there is none, or it has no current reading."""
        for e in await self._mine(alias):
            if e.entity_id.startswith("sensor.") and e.device_class == "power" and e.unit in ("W", "kW"):
                if e.state in NO_READING:
                    return None
                try:
                    value = float(e.state)
                except ValueError:
                    return None
                return value * 1000 if e.unit == "kW" else value
        return None

    async def power_state(self, alias: str) -> PowerState | None:
        """A binary power sensor: 'on', 'off', or 'unknown' (no current reading). None if there is none."""
        for e in await self._mine(alias):
            if e.entity_id.startswith("binary_sensor.") and e.device_class == "power":
                return e.state if e.state in ("on", "off") else "unknown"  # type: ignore[return-value]
        return None

    async def filter_reminder(self, alias: str) -> FilterReminder | None:
        """The Bosch filter-reminder event sensor ('present' or 'confirmed' means due); None if not exposed."""
        for e in await self._mine(alias):
            if e.entity_id.startswith("sensor.") and e.entity_id.endswith(FILTER_REMINDER_SUFFIXES):
                return e.state if e.state in ("confirmed", "present", "off") else None  # type: ignore[return-value]
        return None

    async def operation_state(self, alias: str) -> str | None:
        for e in await self._mine(alias):
            if e.entity_id.startswith("sensor.") and e.entity_id.endswith("_operation_state"):
                return None if e.state in NO_READING else e.state
        return None


def prefix_map(prefixes: dict[str, list[str]]) -> EntityMap:
    """An EntityMap from alias -> entity-id prefixes (e.g. {"dishwasher": ["kitchen_dishwasher"]})."""
    return lambda alias: prefixes.get(alias, [])
