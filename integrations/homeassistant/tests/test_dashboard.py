"""The ready dashboard in a real Home Assistant: every entity it names exists once the integration is set up, and each
of its templates renders to what the card shows (the red and amber lines, "due in 3 days", the link to the job)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from custom_components.fixit_lens.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_integration import (
    TODAY,
    dated,
    home,  # noqa: F401  (the fixture: the home and its dishwasher)
)

BUILT_IN = {"heading", "todo-list", "calendar", "conditional", "markdown", "entities"}


def cards(view: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for section in view["sections"]:
        for c in section["cards"]:
            out += [c, c["card"]] if c["type"] == "conditional" else [c]
    return out


def entities(card: dict[str, Any]) -> list[str]:
    if card["type"] == "conditional":
        return [c["entity"] for c in card["conditions"]]
    if "entity" in card:
        return [card["entity"]]
    return [e if isinstance(e, str) else e["entity"] for e in card.get("entities", [])]


def shows(hass: HomeAssistant, card: dict[str, Any]) -> bool:
    """A conditional card's numeric_state conditions, as the frontend reads them."""
    for c in card["conditions"]:
        v = float(hass.states.get(c["entity"]).state)
        if ("above" in c and not v > c["above"]) or ("below" in c and not v < c["below"]):
            return False
    return True


async def dashboard_for(hass: HomeAssistant, jobs: list[Any]) -> dict[str, Any]:
    from custom_components.fixit_lens.diagnostics import async_get_config_entry_diagnostics

    entry = MockConfigEntry(domain=DOMAIN, unique_id="hh1", data={"url": "https://fixit.test", "token": "fxt_secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.fixit_lens.core.Client.jobs", AsyncMock(return_value=jobs)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    d = await async_get_config_entry_diagnostics(hass, entry)
    assert "fxt_secret" not in repr(d["dashboard"]) and "fixit.test" not in repr(d["dashboard"])
    return dict(d["dashboard"])


async def test_every_entity_it_names_exists_and_its_cards_are_home_assistants_own(hass: HomeAssistant) -> None:
    board = await dashboard_for(hass, [dated("clean_filters", TODAY + timedelta(days=3), "upcoming")])
    [view] = board["views"]
    assert {c["type"] for c in cards(view)} <= BUILT_IN
    named = {e for c in cards(view) for e in entities(c)}
    assert "sensor.dishwasher_next_job" in named and "todo.fix_it_lens_upkeep" in named
    assert all(hass.states.get(e) is not None for e in named), [e for e in named if hass.states.get(e) is None]


async def render(hass: HomeAssistant, card: dict[str, Any]) -> str:
    return str(Template(card["content"], hass).async_render(parse_result=False)).strip()


async def test_an_overdue_job_shows_red_and_the_next_one_links_to_the_app(hass: HomeAssistant) -> None:
    board = await dashboard_for(hass, [dated("clean_filters", TODAY - timedelta(days=2), "overdue"),
                                       dated("fill_salt", TODAY + timedelta(days=3), "upcoming")])
    _, red, amber, _, nxt = board["views"][0]["sections"][1]["cards"]
    assert shows(hass, red) and not shows(hass, amber)
    assert await render(hass, red["card"]) == '<ha-alert alert-type="error">1 overdue: Filters</ha-alert>'
    assert await render(hass, nxt) == ("**Filters**: due 2 days ago\n\n"
                                       "[Open the job in Fix-It Lens](https://fixit.test/#job/a1/clean_filters)")


async def test_a_due_job_shows_amber_and_a_quiet_appliance_neither(hass: HomeAssistant) -> None:
    board = await dashboard_for(hass, [dated("fill_salt", TODAY, "due")])
    _, red, amber, _, nxt = board["views"][0]["sections"][1]["cards"]
    assert shows(hass, amber) and not shows(hass, red)
    assert await render(hass, amber["card"]) == '<ha-alert alert-type="warning">1 due now: Salt</ha-alert>'
    assert (await render(hass, nxt)).startswith("**Salt**: due today")


async def test_no_dated_job_says_so(hass: HomeAssistant) -> None:
    board = await dashboard_for(hass, [dated("descale", None, "none")])
    _, red, amber, _, nxt = board["views"][0]["sections"][1]["cards"]
    assert not shows(hass, red) and not shows(hass, amber)
    assert await render(hass, nxt) == "No job with a date yet."
