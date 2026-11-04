"""The HA custom integration's core against the real household router: it reads /due, names the entities, and writes
nothing but a ticked-off job's last-done date. (The HA platform files need Home Assistant and are not run here.)"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.feeds.errors import install_errors
from app.feeds.router import Deps, make_router
from app.schedule import store
from tests.l5.conftest import T0, add_appliance

# core.py alone, by path: the package's __init__ imports Home Assistant, which this env does not have.
CORE = Path(__file__).resolve().parents[2] / "integrations/homeassistant/custom_components/fixit_lens/core.py"
_spec = importlib.util.spec_from_file_location("fixit_lens_core", CORE)
assert _spec is not None and _spec.loader is not None
core = importlib.util.module_from_spec(_spec)
sys.modules["fixit_lens_core"] = core
_spec.loader.exec_module(core)


def joined(conn: sqlite3.Connection, secret: bytes) -> tuple[str, str]:
    [inv] = store.create_invites(conn, 1, cap_usd_day=0.45, at=T0)
    hid = store.claim_invite(conn, inv.code, consent=True, at=T0)
    assert hid is not None
    return hid, store.household_token(secret, hid)


def client_for(conn: sqlite3.Connection, secret: bytes, loader: Any, token: str) -> core.Client:
    app = FastAPI()
    app.include_router(make_router(Deps(conn, secret, loader, clock=lambda: T0)), prefix="/api/v1")
    install_errors(app)
    return core.Client("https://fixit.test", token, transport=httpx.ASGITransport(app=app))


def test_reads_due_and_names_the_sensors(conn: sqlite3.Connection, secret: bytes, loader: Any) -> None:
    hid, token = joined(conn, secret)
    add_appliance(conn, hid, alias="Washer")
    c = client_for(conn, secret, loader, token)
    jobs = asyncio.run(c.jobs())
    assert jobs, "the Acme card has upkeep jobs"
    j = jobs[0]
    assert j.alias == j.appliance_id  # no GET /appliances on this server yet: the id stands in
    assert j.object_id.endswith("_next_due") and j.object_id == core.slug(j.alias) + "_" + j.job_id + "_next_due"
    assert "manual, p." in j.description() and j.attributes()["interval"] == j.interval_quote


def test_aliases_from_l1s_appliances_route(conn: sqlite3.Connection, secret: bytes, loader: Any,
                                          tmp_path: Path) -> None:
    from app.api import household_routes
    from app.store import Store

    hid, token = joined(conn, secret)
    add_appliance(conn, hid, alias="Washer")
    app = FastAPI()
    app.include_router(household_routes(Store(conn, secret), sessions_root=lambda: tmp_path,
                                        load_card=lambda _: None), prefix="/api/v1")
    app.include_router(make_router(Deps(conn, secret, loader, clock=lambda: T0)), prefix="/api/v1")
    jobs = asyncio.run(core.Client("https://fixit.test", token, transport=httpx.ASGITransport(app=app)).jobs())
    assert jobs and all(j.alias == "Washer" for j in jobs)
    assert jobs[0].object_id.startswith("washer_")


def test_ticking_off_writes_the_users_date_and_the_server_moves_due(conn: sqlite3.Connection, secret: bytes,
                                                                     loader: Any) -> None:
    hid, token = joined(conn, secret)
    add_appliance(conn, hid)
    c = client_for(conn, secret, loader, token)

    async def go() -> tuple[core.Job, core.Job]:
        before = (await c.jobs())[0]
        await c.mark_done(before, T0.date())
        after = next(j for j in await c.jobs() if j.key == before.key)
        return before, after

    _, after = asyncio.run(go())
    assert after.last_done == T0.date() and after.last_done_source == "user"
    assert after.due_on is not None and after.due_on > T0.date()


def test_a_bad_token_is_auth_failed(conn: sqlite3.Connection, secret: bytes, loader: Any) -> None:
    c = client_for(conn, secret, loader, "nope")
    with pytest.raises(core.AuthFailed):
        asyncio.run(c.household())


def test_unreachable() -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    c = core.Client("https://fixit.test", "t", transport=httpx.MockTransport(down))
    with pytest.raises(core.Unreachable):
        asyncio.run(c.jobs())


def test_parse_orders_by_due_and_uses_aliases() -> None:
    rows = [
        {"appliance_id": "a1", "job_id": "salt", "title": "Fill salt", "due_on": None,
         "last_done": {"date": None, "source": "unknown"}, "interval_quote": "from your manual, p. 42", "page": 42},
        {"appliance_id": "a1", "job_id": "clean_filters", "title": "Clean the filters", "due_on": "2026-11-01",
         "last_done": {"date": "2026-09-01", "source": "user"}, "interval_quote": "from your manual, p. 42",
         "page": 42, "duration_min": 10},
    ]
    jobs = core.parse_due(rows, {"a1": "Dish Washer"})
    assert [j.job_id for j in jobs] == ["clean_filters", "salt"]
    assert jobs[0].object_id == "dish_washer_clean_filters_next_due" and jobs[0].due_on == date(2026, 11, 1)
    assert jobs[0].description() == "10 minutes · from your manual, p. 42 · last done 2026-09-01"
    assert jobs[1].description().endswith("last done: not known")


def test_the_platform_files_never_call_a_home_assistant_service() -> None:
    """The integration lives inside HA but reads only our API; it never controls a device. It offers two services of
    its own (mark_done, snooze), which send to Fix-It Lens, and calls none."""
    import re

    root = Path(core.__file__).parent
    for f in root.glob("*.py"):
        text = f.read_text()
        assert not re.search(r"services\.(async_)?call\b|\.async_call\(|call_service", text), f.name
        for m in re.finditer(r"hass\.services\.(\w+)", text):
            assert m.group(1) == "async_register", (f.name, m.group(0))
    registered = re.findall(r"async_register\(DOMAIN, (\w+)", (root / "__init__.py").read_text())
    assert registered == ["SERVICE_MARK_DONE", "SERVICE_SNOOZE"]


def test_the_blueprint_calls_only_fixit_lens_and_the_chosen_phone() -> None:
    import yaml

    class Input(str):
        pass

    yaml.SafeLoader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))
    bp = yaml.safe_load((Path(core.__file__).parents[2] / "blueprints/automation/fixit_lens/job_due_notify.yaml")
                        .read_text())

    def actions(node: Any) -> list[str]:
        if isinstance(node, dict):
            own = [node["action"]] if isinstance(node.get("action"), str) and "." in node["action"] else []
            return own + [a for v in node.values() for a in actions(v)]
        return [a for v in node for a in actions(v)] if isinstance(node, list) else []

    assert set(actions(bp["actions"])) == {"fixit_lens.mark_done", "fixit_lens.snooze"}
    [notify] = [a for a in bp["actions"] if a.get("domain") == "mobile_app"]
    assert notify["type"] == "notify" and notify["device_id"] == "phone"  # the phone the user picked, only


def test_a_home_assistant_token_marks_done_and_snoozes_as_home_assistant(conn: sqlite3.Connection, secret: bytes,
                                                                         loader: Any) -> None:
    hid, token = joined(conn, secret)
    add_appliance(conn, hid, alias="Washer")
    _, ha = store.issue_token(conn, hid, by_kind="ha", scope="write", label="Home Assistant", at=T0)
    _, ro = store.issue_token(conn, hid, by_kind="ha", scope="read", label="Hallway panel", at=T0)
    c = client_for(conn, secret, loader, ha)

    async def go() -> tuple[core.Job, core.Job]:
        job = (await c.jobs())[0]
        await c.mark_done(job, date(2026, 7, 1))  # due 1 Oct: overdue
        late = next(j for j in await c.jobs() if j.key == job.key)
        with pytest.raises(core.Refused, match="only moves it later"):
            await c.snooze(late, date(2026, 9, 1))
        await c.snooze(late, date(2026, 11, 3))
        with pytest.raises(core.Refused, match="can only read"):
            await client_for(conn, secret, loader, ro).mark_done(late, T0.date())
        return late, next(j for j in await c.jobs() if j.key == job.key)

    late, snoozed = asyncio.run(go())
    assert late.status == "overdue" and late.link == f"https://fixit.test/#job/{late.appliance_id}/{late.job_id}"
    assert (snoozed.due_on, snoozed.snoozed_until, snoozed.status) == (date(2026, 11, 3), date(2026, 11, 3),
                                                                       "upcoming")
    who = {r["by_kind"] + "/" + r["by_label"] for r in conn.execute("SELECT by_kind, by_label FROM job_change")}
    assert who == {"ha/Home Assistant"}
    assert "snoozed to 2026-11-03" in snoozed.description()


def test_your_schedule_in_words() -> None:
    assert core.schedule_words({"kind": "window", "min": 3, "max": 6, "unit": "month"}) == "every 3 to 6 months"
    assert core.schedule_words({"kind": "every", "every": 1, "unit": "week"}) == "every week"
    assert core.schedule_words({"kind": "every", "every": 2, "unit": "year"}) == "every 2 years"
    assert core.schedule_words(None) is None


@pytest.mark.parametrize("title", ["Clean the debris filter", "Run the machine-cleaning programme", "Fill salt",
                                   "Pompfilter schoonmaken", "Machinereinigingsprogramma uitvoeren", "Descale.",
                                   "Ontkalken", "Check the door seal"])
def test_home_assistant_names_a_job_as_the_reminder_does(title: str) -> None:
    from types import SimpleNamespace

    from app.schedule.due import due_name

    job = SimpleNamespace(due_name=None, title=SimpleNamespace(root={"en": title}))
    assert core.short_name(title) == due_name(job, "en")  # type: ignore[arg-type]


def test_the_snapshot_has_the_home_and_each_appliances_maker(conn: sqlite3.Connection, secret: bytes, loader: Any,
                                                              tmp_path: Path) -> None:
    from app.api import household_routes
    from app.store import Store

    hid, token = joined(conn, secret)
    aid = add_appliance(conn, hid, alias="dishwasher")
    app = FastAPI()
    app.include_router(household_routes(Store(conn, secret), sessions_root=lambda: tmp_path,
                                        load_card=lambda _: None), prefix="/api/v1")
    app.include_router(make_router(Deps(conn, secret, loader, clock=lambda: T0)), prefix="/api/v1")
    snap = asyncio.run(core.Client("https://fixit.test", token, transport=httpx.ASGITransport(app=app)).snapshot())
    a = snap.appliances[aid]
    assert (a.name, a.kind, a.brand, a.model) == ("Dishwasher", "washer", "acme", "WM-0000")
    assert snap.appliance_ids() == [aid] and snap.jobs and all(j.appliance_id == aid for j in snap.jobs)
    assert snap.jobs[0].summary.startswith("Dishwasher: ") and snap.jobs[0].name[:1].isupper()


@pytest.mark.parametrize(("brand", "maker"), [("bosch", "Bosch"), ("aeg", "AEG"), ("de-dietrich", "De Dietrich"),
                                              ("Miele", "Miele"), (None, None)])
def test_the_device_names_the_maker(brand: str | None, maker: str | None) -> None:
    assert core.Appliance("a1", "washer", brand=brand).maker == maker


def test_a_sensor_is_named_as_the_reminder_names_the_job(conn: sqlite3.Connection, secret: bytes,
                                                         loader: Any) -> None:
    from app.schedule.due import due_name
    from tests.l5.conftest import CARD_PATH

    hid, token = joined(conn, secret)
    add_appliance(conn, hid, alias="washer")
    card = loader(CARD_PATH)
    [lang] = conn.execute("SELECT lang FROM households WHERE id = ?", (hid,)).fetchone()
    jobs = asyncio.run(client_for(conn, secret, loader, token).jobs())
    assert jobs
    for j in jobs:  # in the household's language, as its reminders are
        [cj] = [c for c in card.jobs if c.id == j.job_id]
        assert j.due_name == due_name(cj, lang) and j.name == core.cap(due_name(cj, lang))


# Discovery: the devices Home Assistant knows, offered as a list to tick

@pytest.mark.parametrize(("manufacturer", "model", "name", "source", "kind"), [
    ("BOSCH", "SMV4HAX40N", "Dishwasher", "home_connect", "dishwasher"),
    ("Bosch", "SMV4HAX40N", "Keuken", "home_connect", "dishwasher"),  # no type in the name: the BSH code says it
    ("Siemens", "WM14N2G0NL", "Wasmachine", "home_connect", "washer"),
    ("Bosch", "WTX87M40", "Droger", "home_connect", "dryer"),
    ("Miele", "G 7100 SCi", "Vaatwasser", "miele", "dishwasher"),
    ("Samsung", "WW90T534DAW", "Washing machine", "smartthings", "washer"),
    ("Neff", "B57CR22N0", "Oven", "home_connect", "oven"),
    ("Intergas", "Xtreme 36", "CV-ketel boiler", "mqtt", "boiler"),
    ("LG", "GBB72", "Fridge freezer", "lg_thinq", "fridge"),
])
def test_an_appliance_home_assistant_knows_is_offered_with_its_kind(manufacturer: str, model: str, name: str,
                                                                    source: str, kind: str) -> None:
    f = core.discovered("dev1", manufacturer=manufacturer, model=model, name=name, source=source)
    assert f is not None and f.kind == kind and f.model == model and f.brand == manufacturer.lower()


@pytest.mark.parametrize(("manufacturer", "model", "name", "source", "lights"), [
    ("Signify", "LCT015", "Kitchen light", "hue", False),
    ("IKEA", "TRADFRI bulb E27", "Oven corner", "zha", True),  # only lights: a light, whatever its name says
    ("Bosch", "SMV4HAX40N", "Dishwasher", "home_connect", True),
    (None, "SMV4HAX40N", "Dishwasher", "home_connect", False),  # no maker
    ("Bosch", None, "Dishwasher", "home_connect", False),  # no model
    ("Sonos", "One SL", "Living room", "sonos", False),
    ("Bosch", "SMV4HAX40N", "Keuken", "mqtt", False),  # from elsewhere, its kind must be in its name
])
def test_anything_else_is_not_offered(manufacturer: str | None, model: str | None, name: str, source: str,
                                      lights: bool) -> None:
    assert core.discovered("d", manufacturer=manufacturer, model=model, name=name, source=source,
                           only_lights=lights) is None


def test_the_list_leaves_out_what_the_home_has_and_never_reuses_a_name() -> None:
    dw = core.discovered("d1", manufacturer="BOSCH", model="SMV4HAX40N", name="Dishwasher", source="home_connect")
    wm = core.discovered("d2", manufacturer="Siemens", model="WM14N2G0NL", name="Dishwasher", source="home_connect")
    assert dw is not None and wm is not None
    have = [core.Appliance("a1", "Dishwasher", "dishwasher", "bosch", "SMV4HAX40N/39")]
    [only] = core.offer([dw, wm], have)  # the Bosch is in the home already under its E-Nr
    assert only.device_id == "d2" and only.alias == "Dishwasher WM14N2G0NL"  # "Dishwasher" is the Bosch's
    assert core.offer([dw, dw], []) == [dw]


def test_a_tick_sends_the_maker_model_kind_and_name_only() -> None:
    sent: list[httpx.Request] = []

    def answer(r: httpx.Request) -> httpx.Response:
        sent.append(r)
        return httpx.Response(201, json={"id": "a9", "alias": "Dishwasher"})

    f = core.discovered("ha-device-id", manufacturer="BOSCH", model="SMV4HAX40N", name="Dishwasher",
                        source="home_connect")
    assert f is not None and f.label == "Dishwasher: Bosch SMV4HAX40N"
    client = core.Client("https://fixit.test", "fxt_t", transport=httpx.MockTransport(answer))
    asyncio.run(client.add(f))
    [r] = sent
    assert (r.method, r.url.path) == ("POST", "/api/v1/appliances")
    assert json.loads(r.content) == {"brand": "bosch", "model": "SMV4HAX40N", "kind": "dishwasher",
                                                   "alias": "Dishwasher"}
    assert b"ha-device-id" not in r.content


def test_a_read_only_token_cannot_add() -> None:
    client = core.Client("https://fixit.test", "fxt_t", transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"error": "forbidden", "detail": "read-only"})))
    f = core.Found("d", "bosch", "SMV4HAX40N", "dishwasher", "Dishwasher")
    with pytest.raises(core.ReadOnly):
        asyncio.run(client.add(f))


# The ready dashboard: Home Assistant's own cards, the file in the repo is what the generator writes

def _dashboards_make() -> Any:
    spec = importlib.util.spec_from_file_location("fixit_dashboards_make", CORE.parents[2] / "dashboards/make.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_dashboard_file_is_the_generators_and_uses_only_built_in_cards() -> None:
    import yaml

    make = _dashboards_make()
    text = (CORE.parents[2] / "dashboards/fixit_lens.yaml").read_text()
    assert text == make.render(), "run integrations/homeassistant/dashboards/make.py"
    board = yaml.safe_load(text)
    types = {c["type"] for s in board["views"][0]["sections"] for c in s["cards"]}
    types |= {c["card"]["type"] for s in board["views"][0]["sections"] for c in s["cards"] if "card" in c}
    assert types == {"heading", "todo-list", "calendar", "conditional", "markdown", "entities"}
    assert "custom:" not in text and "fxt_" not in text and "token" not in text.lower()
    headings = [s["cards"][0]["heading"] for s in board["views"][0]["sections"]]
    assert headings == ["To do", "Dishwasher", "Washer", "Boiler", "Fridge", "Oven"]


def test_the_dashboard_names_the_sensors_the_integration_makes() -> None:
    board = core.dashboard(["Washing machine"], "Demo home")
    text = repr(board)
    for e in ("sensor.washing_machine_next_job", "sensor.washing_machine_due_jobs",
              "sensor.washing_machine_overdue_jobs", "todo.fix_it_lens_upkeep", "calendar.fix_it_lens_upkeep"):
        assert e in text
    assert board["title"] == "Demo home"
    assert board["views"][0]["sections"][1]["cards"][0]["icon"] == "mdi:washing-machine"
