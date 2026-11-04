"""The HA layer in a real Home Assistant: config flow, the next-due sensors, the to-do list and the calendar."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.fixit_lens.const import DOMAIN
from custom_components.fixit_lens.core import Appliance, AuthFailed, Job
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

HARNESS_TZ = "US/Pacific"  # the time zone the harness gives hass; asserted in setup()
TODAY = dt_util.now(dt_util.get_time_zone(HARNESS_TZ)).date()  # "today" as the integration takes it, at every hour


def job(job_id: str, due: date | None, alias: str = "Dishwasher") -> Job:
    return Job("a1", alias, job_id, job_id.replace("_", " ").capitalize(), due, None, "never",
               "from your manual, p. 42", 42, 10)


JOBS = [job("clean_filters", TODAY - timedelta(days=1)), job("fill_salt", TODAY + timedelta(days=20)),
        job("descale", None)]


HOME = {"id": "hh1", "name": "Demo home"}
DISHWASHER = Appliance("a1", "dishwasher", "dishwasher", "bosch", "SMV6ZCX42E")


@pytest.fixture(autouse=True)
def home() -> Any:
    """The home and its appliances, as GET /household and /appliances give them; a test patches them to change it."""
    with patch("custom_components.fixit_lens.core.Client.household",
               AsyncMock(return_value=HOME)), \
            patch("custom_components.fixit_lens.core.Client.appliances", AsyncMock(return_value=[DISHWASHER])) as a:
        yield a


@pytest.fixture
def api() -> Any:
    with patch("custom_components.fixit_lens.core.Client.jobs", AsyncMock(return_value=JOBS)) as jobs, \
            patch("custom_components.fixit_lens.core.Client.household", AsyncMock(return_value=HOME)), \
            patch("custom_components.fixit_lens.core.Client.mark_done", AsyncMock()) as done:
        yield jobs, done


async def setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="hh1", data={"url": "https://fixit.test", "token": "t"})
    entry.add_to_hass(hass)
    assert hass.config.time_zone == HARNESS_TZ  # else TODAY is not the integration's today
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, api: Any) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": "https://fixit.test", "token": "t"})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["data"]["token"] == "t"
    assert r["title"] == "Demo home"  # named after the home, so two homes read apart


async def test_config_flow_bad_token(hass: HomeAssistant) -> None:
    with patch("custom_components.fixit_lens.core.Client.household", AsyncMock(side_effect=AuthFailed("no"))):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": "https://x.test", "token": "bad"})
    assert r["type"] is FlowResultType.FORM and r["errors"] == {"base": "invalid_auth"}


def addon(url: str = "http://local-fixit-lens:8790", token: str = "fxt_from_addon") -> HassioServiceInfo:
    return HassioServiceInfo(config={"url": url, "token": token}, name="Fix-It Lens", slug="local_fixit_lens",
                             uuid="1234")


async def test_the_addon_is_discovered_and_added_with_one_press(hass: HomeAssistant, api: Any) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_HASSIO},
                                                  data=addon())
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "hassio_confirm"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"] == {"url": "http://local-fixit-lens:8790", "token": "fxt_from_addon"}
    assert r["result"].unique_id == "hh1"


async def test_connect_again_gives_the_home_already_here_its_new_key(hass: HomeAssistant, api: Any) -> None:
    entry = await setup(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_HASSIO},
                                                  data=addon(token="fxt_new"))
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"
    assert entry.data == {"url": "http://local-fixit-lens:8790", "token": "fxt_new"}


async def test_a_refused_addon_key_offers_nothing(hass: HomeAssistant) -> None:
    with patch("custom_components.fixit_lens.core.Client.household", AsyncMock(side_effect=AuthFailed("no"))):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_HASSIO},
                                                      data=addon(token="bad"))
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "invalid_auth"
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_next_due_sensors(hass: HomeAssistant, api: Any) -> None:
    await setup(hass)
    s = hass.states.get("sensor.dishwasher_clean_filters_next_due")
    assert s is not None and s.state == (TODAY - timedelta(days=1)).isoformat()
    assert s.attributes["interval"] == "from your manual, p. 42" and s.attributes["last_done_source"] == "never"
    assert hass.states.get("sensor.dishwasher_descale_next_due").state == "unknown"


async def test_todo_lists_due_items_and_ticking_sends_last_done(hass: HomeAssistant, api: Any) -> None:
    jobs, done = api
    await setup(hass)
    todo = hass.states.get("todo.fix_it_lens_upkeep")
    assert todo is not None and todo.state == "2"  # overdue filters + the never-answered descale
    await hass.services.async_call("todo", "update_item", {"item": "a1:clean_filters", "status": "completed"},
                                   target={"entity_id": "todo.fix_it_lens_upkeep"}, blocking=True)
    done.assert_awaited_once()
    assert done.await_args.args[0].job_id == "clean_filters" and done.await_args.args[1] == TODAY


async def test_calendar_events(hass: HomeAssistant, api: Any) -> None:
    await setup(hass)
    cal = hass.states.get("calendar.fix_it_lens_upkeep")
    assert cal is not None and cal.attributes["message"] == "Dishwasher: Filters"
    assert cal.attributes["description"].startswith("Clean filters\n")
    r = await hass.services.async_call(
        "calendar", "get_events", {"start_date_time": dt_util.now() - timedelta(days=2),
                                   "end_date_time": dt_util.now() + timedelta(days=30)},
        target={"entity_id": "calendar.fix_it_lens_upkeep"}, blocking=True, return_response=True)
    assert [e["summary"] for e in r["calendar.fix_it_lens_upkeep"]["events"]] == \
        ["Dishwasher: Filters", "Dishwasher: Salt"]


async def test_refused_token_starts_reauth(hass: HomeAssistant) -> None:
    with patch("custom_components.fixit_lens.core.Client.jobs", AsyncMock(side_effect=AuthFailed("refused"))):
        entry = MockConfigEntry(domain=DOMAIN, unique_id="hh1", data={"url": "https://fixit.test", "token": "t"})
        entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert [f["context"]["source"] for f in hass.config_entries.flow.async_progress()] == ["reauth"]


def dated(job_id: str, due: date | None, status: str, **kw: Any) -> Job:
    return Job("a1", "Dishwasher", job_id, job_id.replace("_", " ").capitalize(), due, None, "never",
               "from your manual, p. 42", 42, 10, status=status, link=f"https://fixit.test/#job/a1/{job_id}", **kw)


async def test_sensors_carry_status_window_and_the_job(hass: HomeAssistant) -> None:
    window = dated("descale", TODAY - timedelta(days=3), "due", window_end=TODAY + timedelta(days=60),
                   schedule="every 3 to 6 months")
    with patch("custom_components.fixit_lens.core.Client.jobs", AsyncMock(return_value=[window])), \
            patch("custom_components.fixit_lens.core.Client.household", AsyncMock(return_value=HOME)):
        await setup(hass)
        a = hass.states.get("sensor.dishwasher_descale_next_due").attributes
        assert (a["status"], a["window_end"], a["your_schedule"], a["job"]) == (
            "due", (TODAY + timedelta(days=60)).isoformat(), "every 3 to 6 months", "a1:descale")
        assert a["link"] == "https://fixit.test/#job/a1/descale"
        r = await hass.services.async_call(
            "calendar", "get_events", {"start_date_time": dt_util.now() + timedelta(days=30),
                                       "end_date_time": dt_util.now() + timedelta(days=31)},
            target={"entity_id": "calendar.fix_it_lens_upkeep"}, blocking=True, return_response=True)
        [e] = r["calendar.fix_it_lens_upkeep"]["events"]  # the window spans its whole range
        assert e["end"] == (TODAY + timedelta(days=61)).isoformat()


async def test_a_job_falling_due_fires_one_event(hass: HomeAssistant) -> None:
    from custom_components.fixit_lens.const import EVENT_JOB_DUE
    from homeassistant.core import Event

    fired: list[Event] = []
    hass.bus.async_listen(EVENT_JOB_DUE, fired.append)
    soon = dated("clean_filters", TODAY + timedelta(days=1), "upcoming")
    now = dated("clean_filters", TODAY, "due")
    jobs = AsyncMock(return_value=[soon])
    with patch("custom_components.fixit_lens.core.Client.jobs", jobs), \
            patch("custom_components.fixit_lens.core.Client.household", AsyncMock(return_value=HOME)):
        entry = await setup(hass)
        assert fired == []
        jobs.return_value = [now]
        for _ in range(2):  # polled twice: announced once
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
        assert [e.data for e in fired] == [now.event()]
        assert fired[0].data["job"] == "a1:clean_filters" and fired[0].data["link"].endswith("/#job/a1/clean_filters")
        await hass.config_entries.async_reload(entry.entry_id)  # a restart remembers what it announced
        await hass.async_block_till_done()
        assert len(fired) == 1
        jobs.return_value = [dated("clean_filters", TODAY, "overdue")]
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert [e.data["status"] for e in fired] == ["due", "overdue"]


async def test_mark_done_and_snooze_services(hass: HomeAssistant) -> None:
    from custom_components.fixit_lens.core import Refused
    from homeassistant.exceptions import ServiceValidationError

    late = dated("clean_filters", TODAY - timedelta(days=2), "overdue")
    with patch("custom_components.fixit_lens.core.Client.jobs", AsyncMock(return_value=[late])), \
            patch("custom_components.fixit_lens.core.Client.household", AsyncMock(return_value=HOME)), \
            patch("custom_components.fixit_lens.core.Client.mark_done", AsyncMock()) as done, \
            patch("custom_components.fixit_lens.core.Client.snooze", AsyncMock()) as snooze:
        await setup(hass)
        await hass.services.async_call(DOMAIN, "mark_done", {"job": "a1:clean_filters"}, blocking=True)
        assert done.await_args.args[1] == TODAY
        await hass.services.async_call(DOMAIN, "snooze", {"job": "a1:clean_filters", "days": 7}, blocking=True)
        assert snooze.await_args.args[1] == TODAY + timedelta(days=7)  # overdue: a week from today
        with pytest.raises(ServiceValidationError, match="No Fix-It Lens job"):
            await hass.services.async_call(DOMAIN, "mark_done", {"job": "a1:nope"}, blocking=True)
        snooze.side_effect = Refused("a snooze only moves it later")
        with pytest.raises(ServiceValidationError, match="only moves it later"):
            await hass.services.async_call(DOMAIN, "snooze", {"job": "a1:clean_filters", "until": "2020-01-01"},
                                           blocking=True)


async def until(cond: Any, s: float = 2.0) -> None:
    """Let the loop run until cond() or about `s` seconds' worth of turns. It yields without a timer, which a frozen
    clock (freezer) would never fire."""
    import asyncio

    for _ in range(int(s * 1000)):
        if cond():
            return
        await asyncio.sleep(0)


def blueprint(inputs: dict[str, Any]) -> dict[str, Any]:
    """The blueprint, with these inputs, as an automation's config."""
    from pathlib import Path

    from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
    from homeassistant.components.blueprint import models
    from homeassistant.util.yaml import load_yaml_dict

    path = Path(__file__).parents[1] / "blueprints/automation/fixit_lens/job_due_notify.yaml"
    bp = models.Blueprint(load_yaml_dict(str(path)), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    assert set(bp.inputs) == {"phone", "snooze_days", "quiet_start", "quiet_end", "wait_hours"}
    config = models.BlueprintInputs(bp, {"use_blueprint": {"path": "x", "input": inputs}}).async_substitute()
    return {**config, "id": "fixit"}


def at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    """A time of day in the harness's time zone, `days` after today."""
    return datetime.combine(TODAY + timedelta(days=days), time(hour, minute), dt_util.get_time_zone(HARNESS_TZ))


@contextmanager
def phone() -> Iterator[tuple[AsyncMock, AsyncMock]]:
    """The phone's notify (a mobile_app device action) stubbed, as this harness has no phone; and mark_done."""
    late = dated("clean_filters", TODAY - timedelta(days=2), "overdue")
    sent = AsyncMock()
    with patch("custom_components.fixit_lens.core.Client.jobs", AsyncMock(return_value=[late])), \
            patch("custom_components.fixit_lens.core.Client.household", AsyncMock(return_value=HOME)), \
            patch("custom_components.fixit_lens.core.Client.mark_done", AsyncMock()) as done, \
            patch("homeassistant.helpers.script.device_action.async_validate_action_config",
                  AsyncMock(side_effect=lambda hass, c: c)), \
            patch("homeassistant.helpers.script.device_action.async_call_action_from_config", sent):
        yield sent, done


async def start(hass: HomeAssistant, inputs: dict[str, Any]) -> None:
    from homeassistant.setup import async_setup_component

    await setup(hass)  # first: its first poll announces the overdue job, before the automation listens
    assert await async_setup_component(hass, "automation", {"automation": [blueprint(inputs)]})
    await hass.async_block_till_done()


DUE_EVENT = dated("clean_filters", TODAY - timedelta(days=2), "overdue").event()


async def test_the_blueprint_sends_a_done_button_that_marks_it_done(hass: HomeAssistant,
                                                                     freezer: FrozenDateTimeFactory) -> None:
    """The blueprint as an automation: a due job notifies the phone with the job, its appliance and a link to it in the
    app, with Done / In 7 days / Open; tapping Done calls fixit_lens.mark_done for that job."""
    from homeassistant.helpers.template import Template

    freezer.move_to(at(12))
    with phone() as (sent, done):
        await start(hass, {"phone": "d1"})
        hass.bus.async_fire("fixit_lens_job_due", DUE_EVENT)
        await until(lambda: sent.await_count)  # not block_till_done: the run waits for the tap
        sent.assert_awaited_once()
        notify, variables = sent.await_args.args[1], sent.await_args.args[2]
        assert (notify["domain"], notify["type"], notify["device_id"]) == ("mobile_app", "notify", "d1")

        def render(s: str) -> str:
            return str(Template(s, hass).async_render(variables.as_dict() if hasattr(variables, "as_dict")
                                                      else dict(variables)))

        assert render(notify["title"]) == "Dishwasher: Clean filters"
        assert render(notify["message"]).startswith("Overdue.")
        assert render(notify["data"]["url"]) == "https://fixit.test/#job/a1/clean_filters"
        assert [render(a["title"]) for a in notify["data"]["actions"]] == ["Done", "In 7 days", "Open"]
        assert variables["job"] == "a1:clean_filters"
        hass.bus.async_fire("mobile_app_notification_action", {"action": variables["done_action"]})
        await until(lambda: done.await_count)
        done.assert_awaited_once()
        assert done.await_args.args[0].key == "a1:clean_filters" and done.await_args.args[1] == TODAY


@pytest.mark.parametrize(("now", "inputs", "sent_at"), [
    (at(23), {}, at(8, days=1)),                                   # past midnight: held until 08:00 the next day
    (at(6, 30), {}, at(8)),                                        # early morning: held until 08:00
    (at(23), {"quiet_start": "22:00:00", "quiet_end": "22:00:00"}, at(23)),  # the same time: no quiet hours
])
async def test_the_blueprint_holds_a_notification_in_quiet_hours(hass: HomeAssistant, freezer: FrozenDateTimeFactory,
                                                                 now: datetime, inputs: dict[str, str],
                                                                 sent_at: datetime) -> None:
    freezer.move_to(now)
    with phone() as (sent, _):
        await start(hass, {"phone": "d1", **inputs})
        hass.bus.async_fire("fixit_lens_job_due", DUE_EVENT)
        await until(lambda: sent.await_count, s=0.5)
        if sent_at == now:
            sent.assert_awaited_once()
            return
        sent.assert_not_awaited()
        freezer.move_to(sent_at - timedelta(minutes=1))
        async_fire_time_changed(hass)
        await until(lambda: sent.await_count, s=0.3)
        sent.assert_not_awaited()  # a minute before the end, still quiet
        freezer.move_to(sent_at)
        async_fire_time_changed(hass)
        await until(lambda: sent.await_count)
        sent.assert_awaited_once()


async def test_each_appliance_is_a_device_under_the_home(hass: HomeAssistant, api: Any) -> None:
    entry = await setup(hass)
    devices = dr.async_get(hass)
    home = devices.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert home is not None and home.name == "Demo home" and home.entry_type is dr.DeviceEntryType.SERVICE
    assert (home.manufacturer, home.model, home.configuration_url) == ("Fix-It Lens", "Home", "https://fixit.test")
    washer = devices.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}:a1")})
    assert washer is not None and washer.via_device_id == home.id
    assert (washer.name, washer.manufacturer, washer.model) == ("Dishwasher", "Bosch", "SMV6ZCX42E")
    entities = er.async_get(hass)
    on = {e.entity_id: e.device_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)}
    assert on["todo.fix_it_lens_upkeep"] == on["calendar.fix_it_lens_upkeep"] == home.id
    assert {k for k, v in on.items() if v == washer.id} == {
        "sensor.dishwasher_clean_filters_next_due", "sensor.dishwasher_fill_salt_next_due",
        "sensor.dishwasher_descale_next_due", "sensor.dishwasher_next_job", "sensor.dishwasher_overdue_jobs",
        "sensor.dishwasher_due_jobs"}


async def test_names_are_short_and_the_due_sensor_is_a_date(hass: HomeAssistant, api: Any) -> None:
    await setup(hass)
    s = hass.states.get("sensor.dishwasher_clean_filters_next_due")
    assert s.attributes["friendly_name"] == "Dishwasher Filters due" and s.attributes["device_class"] == "date"
    assert s.attributes["title"] == "Clean filters"  # the card's full title stays in the attributes
    assert hass.states.get("sensor.dishwasher_fill_salt_next_due").attributes["friendly_name"] == "Dishwasher Salt due"
    assert hass.states.get("todo.fix_it_lens_upkeep").attributes["friendly_name"] == "Demo home Upkeep"


async def test_next_job_and_overdue_count_per_appliance(hass: HomeAssistant) -> None:
    jobs = [dated("clean_filters", TODAY - timedelta(days=9), "overdue"),
            dated("fill_salt", TODAY + timedelta(days=4), "upcoming"),
            dated("run_the_machine_cleaning_programme", TODAY - timedelta(days=20), "overdue"),
            dated("descale", None, "none")]
    with patch("custom_components.fixit_lens.core.Client.jobs", AsyncMock(return_value=jobs)):
        await setup(hass)
        nxt = hass.states.get("sensor.dishwasher_next_job")
        assert nxt.state == "Machine cleaning programme"  # the soonest date, overdue first
        assert nxt.attributes["friendly_name"] == "Dishwasher Next job"
        assert nxt.attributes["due_on"] == (TODAY - timedelta(days=20)).isoformat()
        assert nxt.attributes["job"] == "a1:run_the_machine_cleaning_programme"
        late = hass.states.get("sensor.dishwasher_overdue_jobs")
        assert late.state == "2" and late.attributes["friendly_name"] == "Dishwasher Overdue jobs"
        assert late.attributes["jobs"] == ["Filters", "Machine cleaning programme"]
        assert "unit_of_measurement" not in late.attributes  # HA has no plural for a unit: "1 jobs"


async def test_a_card_short_name_wins_and_a_dutch_title_shortens(hass: HomeAssistant) -> None:
    jobs = [Job("a1", "vaatwasser", "machine_clean", "Machinereinigingsprogramma uitvoeren", TODAY, None, "never",
                None, 42, 90, due_name="machine clean"),
            Job("a1", "vaatwasser", "filter", "Pompfilter schoonmaken", TODAY, None, "never", None, 30, 5)]
    with patch("custom_components.fixit_lens.core.Client.jobs", AsyncMock(return_value=jobs)), \
            patch("custom_components.fixit_lens.core.Client.appliances",
                  AsyncMock(return_value=[Appliance("a1", "vaatwasser", "dishwasher", "Bosch", None)])):
        await setup(hass)
        names = sorted(s.attributes["friendly_name"] for s in hass.states.async_all("sensor")
                       if s.entity_id.endswith("_next_due"))
    assert names == ["Vaatwasser Machine clean due", "Vaatwasser Pompfilter due"]


async def later(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_a_job_done_in_the_app_shows_within_a_minute(hass: HomeAssistant, api: Any, home: Any,
                                                           freezer: FrozenDateTimeFactory) -> None:
    jobs, _ = api
    jobs.return_value = [dated("clean_filters", TODAY - timedelta(days=1), "overdue")]
    await setup(hass)
    assert hass.states.get("sensor.dishwasher_overdue_jobs").state == "1"
    jobs.return_value = [dated("clean_filters", TODAY + timedelta(days=90), "upcoming")]  # done on the phone
    reads = home.await_count
    await later(hass, freezer, 1)
    assert hass.states.get("sensor.dishwasher_overdue_jobs").state == "0"
    assert hass.states.get("sensor.dishwasher_clean_filters_next_due").state == (TODAY + timedelta(days=90)).isoformat()
    assert home.await_count == reads  # only /due each minute; the appliances wait for their half hour
    await later(hass, freezer, 30)
    assert home.await_count > reads


async def test_a_new_appliance_gets_its_device_at_the_next_minute(hass: HomeAssistant, api: Any, home: Any,
                                                                  freezer: FrozenDateTimeFactory) -> None:
    jobs, _ = api
    entry = await setup(hass)
    washer = Appliance("a2", "washer", "washing_machine", "miele", "WWD020")
    home.return_value = [DISHWASHER, washer]
    jobs.return_value = [*JOBS, Job("a2", "washer", "clean_drum", "Clean the drum", TODAY + timedelta(days=5), None,
                                    "never", "from your manual, p. 9", 9, 20)]
    await later(hass, freezer, 1)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}:a2")})
    assert device is not None and device.name == "Washer" and device.manufacturer == "Miele"
    assert hass.states.get("sensor.washer_next_job").state == "Drum"


async def test_an_appliance_the_home_no_longer_has_loses_its_device(hass: HomeAssistant, api: Any, home: Any,
                                                                    freezer: FrozenDateTimeFactory) -> None:
    jobs, _ = api
    entry = await setup(hass)
    devices = dr.async_get(hass)
    assert devices.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}:a1")}) is not None
    home.return_value, jobs.return_value = [], []
    await later(hass, freezer, 30)  # /due alone cannot tell a removed appliance from one with nothing due
    assert devices.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}:a1")}) is None
    assert devices.async_get_device(identifiers={(DOMAIN, entry.entry_id)}) is not None


async def test_dutch_names(hass: HomeAssistant, api: Any) -> None:
    hass.config.language = "nl"
    await setup(hass)
    assert hass.states.get("sensor.dishwasher_next_job").attributes["friendly_name"] == "Dishwasher Volgende klus"
    assert hass.states.get("sensor.dishwasher_clean_filters_next_due").attributes["friendly_name"] == \
        "Dishwasher Filters gepland"
    assert hass.states.get("todo.fix_it_lens_upkeep").attributes["friendly_name"] == "Demo home Onderhoud"


@pytest.mark.parametrize(("title", "after"), [("Fix-It Lens", "Demo home"), ("Our flat", "Our flat")])
async def test_an_older_entry_takes_the_homes_name(hass: HomeAssistant, api: Any, title: str, after: str) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="hh1", title=title,
                            data={"url": "https://fixit.test", "token": "t"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.title == after  # the default name gives way; a name of the user's own stays


async def test_diagnostics_hold_the_schedule_but_not_the_token_or_address(hass: HomeAssistant, api: Any) -> None:
    from custom_components.fixit_lens.diagnostics import async_get_config_entry_diagnostics

    jobs, _ = api
    jobs.return_value = [dated("clean_filters", TODAY, "due")]
    entry = MockConfigEntry(domain=DOMAIN, unique_id="hh1", data={"url": "https://fixit.test", "token": "fxt_secret"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    d = await async_get_config_entry_diagnostics(hass, entry)
    assert d["entry"]["data"] == {"url": "**REDACTED**", "token": "**REDACTED**"}
    assert d["home"] == "Demo home" and d["appliances"][0]["brand"] == "bosch"
    assert d["jobs"][0]["job"] == "a1:clean_filters" and d["jobs"][0]["link"] == "**REDACTED**"
    assert "fxt_secret" not in repr(d) and "fixit.test" not in repr(d)
