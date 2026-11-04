"""app/ha is read-only: GET only, never a service; and what it reads for the power witness and the job start."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ha.client import HA, ReadOnlyTransport, prefix_map

ROOT = Path(__file__).resolve().parents[2]

STATES: list[dict[str, Any]] = [
    {"entity_id": "sensor.kitchen_dishwasher_operation_state", "state": "ready",
     "attributes": {"device_class": "enum"}},
    {"entity_id": "sensor.kitchen_dishwasher_smart_filter_cleaning_reminder", "state": "present",
     "attributes": {"device_class": "enum"}},
    {"entity_id": "sensor.laundry_room_washing_machine_power", "state": "0",
     "attributes": {"device_class": "power", "unit_of_measurement": "W"}},
    {"entity_id": "binary_sensor.laundry_room_washing_machine_power", "state": "off",
     "attributes": {"device_class": "power"}},
    {"entity_id": "sensor.boiler_pressure", "state": "unavailable",
     "attributes": {"device_class": "power", "unit_of_measurement": "W"}},
]


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self, states: list[dict[str, Any]] | None = None) -> None:
        self.states = STATES if states is None else states
        self.seen: list[tuple[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((request.method, request.url.path))
        if request.method != "GET":
            pytest.fail(f"app/ha sent {request.method} {request.url.path}")
        if request.url.path == "/api/":
            return httpx.Response(200, json={"message": "API running."})
        return httpx.Response(200, json=self.states)


def ha(tmp_path: Path, rec: httpx.AsyncBaseTransport) -> HA:
    (tmp_path / "ha.token").write_text("t0ken\n")
    return HA(prefix_map({"dishwasher": ["kitchen_dishwasher"], "washer": ["laundry_room_washing_machine"],
                          "boiler": ["sensor.boiler_pressure"], "fridge": []}),
              base_url="http://ha.test:8123", token_path=tmp_path / "ha.token", transport=rec)


def test_reads_for_the_witness_and_the_job_start(tmp_path: Path) -> None:
    rec = Recorder()
    h = ha(tmp_path, rec)
    assert asyncio.run(h.power_w("washer")) == 0.0
    assert asyncio.run(h.power_state("washer")) == "off"
    assert asyncio.run(h.filter_reminder("dishwasher")) == "present"
    assert asyncio.run(h.operation_state("dishwasher")) == "ready"
    ok, ents = asyncio.run(h.entities("dishwasher"))
    assert ok and {e["entity_id"] for e in ents} == {"sensor.kitchen_dishwasher_operation_state",
                                                     "sensor.kitchen_dishwasher_smart_filter_cleaning_reminder"}
    assert set(ents[0]) == {"entity_id", "state", "unit", "last_changed"}
    # A power sensor with no current reading is not a witness of 0 W.
    assert asyncio.run(h.power_w("boiler")) is None
    # No Home Assistant for an appliance: nothing is read, and nothing is claimed.
    n = len(rec.seen)
    assert asyncio.run(h.entities("fridge")) == (False, [])
    assert asyncio.run(h.power_w("fridge")) is None and asyncio.run(h.power_state("fridge")) is None
    assert len(rec.seen) == n
    assert all(m == "GET" for m, _ in rec.seen)


def test_ha_down_is_unavailable_not_an_error(tmp_path: Path) -> None:
    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

    h = ha(tmp_path, Down())
    assert asyncio.run(h.entities("washer")) == (False, [])
    assert asyncio.run(h.power_w("washer")) is None
    assert asyncio.run(h.available()) is False


@pytest.mark.parametrize(("method", "path"), [("POST", "/api/services/switch/turn_off"), ("POST", "/api/states/x"),
                                              ("PUT", "/api/states/x"), ("DELETE", "/api/states/x"),
                                              ("GET", "/api/services"), ("POST", "/api/events/x")])
def test_transport_refuses_any_write(method: str, path: str) -> None:
    rec = Recorder()

    async def go() -> None:
        async with httpx.AsyncClient(transport=ReadOnlyTransport(rec), base_url="http://ha.test") as c:
            await c.request(method, path, content=json.dumps({}))

    with pytest.raises(PermissionError):
        asyncio.run(go())
    assert rec.seen == []


def test_no_write_in_the_source() -> None:
    """A test fails if app/ha ever names a write: an HTTP verb other than GET, or a service or event endpoint."""
    bad = re.compile(r"\.(post|put|patch|delete)\(|/api/(services|events|config|template)|call_service|"
                     r"method\s*=\s*['\"](POST|PUT|PATCH|DELETE)", re.I)
    for p in (ROOT / "app/ha").rglob("*.py"):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            assert not bad.search(line), f"{p.relative_to(ROOT)}:{n}: {line.strip()}"
