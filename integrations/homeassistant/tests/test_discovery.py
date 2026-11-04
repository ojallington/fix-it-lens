"""Discovery in a real Home Assistant: the appliances its device registry knows, offered to tick at setup and under
Configure. Nothing leaves Home Assistant before the user submits, and then only a ticked one's maker, model, kind and
name."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from custom_components.fixit_lens.const import DOMAIN
from custom_components.fixit_lens.core import ReadOnly
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_integration import (
    HOME,
    api,  # noqa: F401  (the fixture: the home's jobs)
    setup,
)


def device(hass: HomeAssistant, domain: str, ident: str, **kw: Any) -> dr.DeviceEntry:
    entry = MockConfigEntry(domain=domain)
    entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(domain, ident)}, **kw)


@pytest.fixture(autouse=True)
def home() -> Any:
    """The home as GET /household gives it, with no appliances yet; a test sets the ones it has."""
    with patch("custom_components.fixit_lens.core.Client.household", AsyncMock(return_value=HOME)), \
            patch("custom_components.fixit_lens.core.Client.appliances", AsyncMock(return_value=[])) as a:
        yield a


@pytest.fixture
def house(hass: HomeAssistant) -> dict[str, dr.DeviceEntry]:
    """Home Connect's dishwasher (as it registers one: maker, the name the app gave, the base model), a lamp."""
    dw = device(hass, "home_connect", "BOSCH-SMV4HAX40N-68A40E000000", manufacturer="BOSCH", name="Dishwasher",
                model="SMV4HAX40N")
    lamp = device(hass, "hue", "lamp1", manufacturer="Signify Netherlands B.V.", name="Kitchen light",
                  model="LCT015")
    er.async_get(hass).async_get_or_create("light", "hue", "lamp1", device_id=lamp.id)
    return {"dishwasher": dw, "lamp": lamp}


@pytest.fixture
def sent() -> Any:
    """Every request the integration makes below GET /household and /appliances (patched): the adds."""
    with patch("custom_components.fixit_lens.core.Client._call",
               AsyncMock(return_value=httpx.Response(201, json={"id": "a2", "alias": "Dishwasher"}))) as call:
        yield call


def adds(call: AsyncMock) -> list[tuple[str, str]]:
    """What was sent beyond reading the home's own schedule (the new entry's GET /due)."""
    return [(c.args[0], c.args[1]) for c in call.await_args_list if c.args[0] != "GET"]


def bodies(call: AsyncMock) -> list[dict[str, Any]]:
    return [json.loads(json.dumps(c.kwargs["json"])) for c in call.await_args_list if c.args[0] != "GET"]


async def test_setup_offers_the_dishwasher_and_adds_only_what_is_ticked(hass: HomeAssistant, house: Any,
                                                                        sent: AsyncMock, home: Any) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": "https://fixit.test", "token": "t"})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "discover"
    offered = r["data_schema"].schema["devices"].options
    assert offered == {house["dishwasher"].id: "Dishwasher: Bosch SMV4HAX40N"}  # the lamp is not offered
    assert r["data_schema"]({}) == {"devices": []}  # nothing ticked at first
    assert sent.await_count == 0  # nothing has left Home Assistant

    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"devices": [house["dishwasher"].id]})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert adds(sent) == [("POST", "/appliances")]
    assert bodies(sent) == [{"brand": "bosch", "model": "SMV4HAX40N", "kind": "dishwasher", "alias": "Dishwasher"}]


async def test_tick_none_and_nothing_is_sent(hass: HomeAssistant, house: Any, sent: AsyncMock, home: Any) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": "https://fixit.test", "token": "t"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] is FlowResultType.CREATE_ENTRY and adds(sent) == []


@pytest.mark.usefixtures("api")
async def test_configure_offers_what_the_home_lacks_and_reads_the_home_after(hass: HomeAssistant, house: Any,
                                                                             home: Any) -> None:
    entry = await setup(hass)
    reads = home.await_count
    with patch("custom_components.fixit_lens.core.Client._call",
               AsyncMock(return_value=httpx.Response(201, json={}))) as call:
        r = await hass.config_entries.options.async_init(entry.entry_id)
        assert r["type"] is FlowResultType.FORM and r["step_id"] == "discover" and call.await_count == 0
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"devices": [house["dishwasher"].id]})
    assert r["type"] is FlowResultType.CREATE_ENTRY and len(bodies(call)) == 1
    assert home.await_count > reads  # the new appliance is read at once, not at the next half hour


@pytest.mark.usefixtures("api")
async def test_configure_has_nothing_to_offer_once_the_home_has_it(hass: HomeAssistant, house: Any,
                                                                   home: Any) -> None:
    from custom_components.fixit_lens.core import Appliance

    home.return_value = [Appliance("a1", "Dishwasher", "dishwasher", "bosch", "SMV4HAX40N/39")]
    entry = await setup(hass)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "nothing_found"


@pytest.mark.usefixtures("api")
async def test_a_read_only_token_says_so_and_adds_nothing(hass: HomeAssistant, house: Any) -> None:
    entry = await setup(hass)
    with patch("custom_components.fixit_lens.core.Client._call", AsyncMock(side_effect=ReadOnly("read"))):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"devices": [house["dishwasher"].id]})
    assert r["type"] is FlowResultType.FORM and r["errors"] == {"base": "read_only"}
