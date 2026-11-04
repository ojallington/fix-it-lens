"""Every example in contracts/examples/ validates in Python (jsonschema and pydantic); every
example under invalid/ is rejected. The TypeScript half is contracts/ts/test/examples.test.ts."""

from __future__ import annotations

import importlib
import json
import sqlite3
import typing
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import ValidationError
from pydantic import BaseModel, TypeAdapter

import contracts
from contracts import unions

EXAMPLES = Path(contracts.__file__).parent / "examples"
VALID = sorted(p for p in EXAMPLES.glob("*/*.json"))
INVALID = sorted(EXAMPLES.glob("*/invalid/*.json"))


def _target(path: Path) -> tuple[str, str | None]:
    name = path.parent.name if path.parent.name != "invalid" else path.parent.parent.name
    stem = path.stem
    definition = stem.split("--", 1)[0] if "--" in stem else None
    return name, definition


def _model(name: str, definition: str | None) -> Any:
    mod = importlib.import_module(f"contracts.models.{name}")
    attr = definition or contracts.schema(name)["title"]
    if hasattr(unions, attr):
        return TypeAdapter(getattr(unions, attr))
    return getattr(mod, attr)


def test_examples_exist_for_every_schema() -> None:
    assert {_target(p)[0] for p in VALID} == set(contracts.NAMES)
    assert len(INVALID) >= 10


@pytest.mark.parametrize("path", VALID, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_valid_example(path: Path) -> None:
    name, definition = _target(path)
    data = json.loads(path.read_text())
    contracts.validate(name, data, definition)
    model = _model(name, definition)
    parsed = model.validate_python(data) if isinstance(model, TypeAdapter) else model.model_validate(data)
    # round trip: dump and validate again
    dumped = (model.dump_python(parsed, mode="json", exclude_unset=True) if isinstance(model, TypeAdapter)
              else parsed.model_dump(mode="json", exclude_unset=True))
    contracts.validate(name, dumped, definition)


@pytest.mark.parametrize("path", INVALID, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_invalid_example(path: Path) -> None:
    name, definition = _target(path)
    with pytest.raises(ValidationError):
        contracts.validate(name, json.loads(path.read_text()), definition)


def _members(adapter_type: Any) -> set[str]:
    union = typing.get_args(adapter_type)[0]
    return {t.__name__ for t in typing.get_args(union)}


def test_unions_match_schemas() -> None:
    def refs(name: str, definition: str) -> set[str]:
        return {r["$ref"].rsplit("/", 1)[1] for r in contracts.schema(name)["$defs"][definition]["oneOf"]}

    assert _members(unions.SafetyState) == {"Ok", "Hold", "Stop"} == refs("safety", "SafetyState")
    assert _members(unions.C2S) == refs("ws", "C2S")
    assert _members(unions.S2C) == refs("ws", "S2C")


def test_every_ws_message_has_seq() -> None:
    defs = contracts.schema("ws")["$defs"]
    for name in ("C2S", "S2C"):
        for ref in defs[name]["oneOf"]:
            d = defs[ref["$ref"].rsplit("/", 1)[1]]
            assert {"type", "seq", "t_ms"} <= set(d["required"]), ref


def test_held_never_carries_text() -> None:
    held = contracts.schema("ws")["$defs"]["Held"]
    assert held["additionalProperties"] is False and "text" not in held["properties"]


def test_store_sql_migrates() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(contracts.STORE_SQL.read_text())
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert {"households", "appliances", "cards_ref", "job_state", "history", "sessions", "pilot_events"} <= tables
    assert db.execute("pragma user_version").fetchone() == (14,)


def test_openapi_refs_resolve() -> None:
    api = yaml.safe_load(contracts.OPENAPI.read_text())
    assert api["openapi"].startswith("3.1")
    text = contracts.OPENAPI.read_text()
    refs = {line.split("$ref:")[1].split("}")[0].strip().strip('"') for line in text.splitlines() if "$ref:" in line}
    for ref in refs:
        file, _, pointer = ref.partition("#")
        doc: Any = api if not file else contracts.schema(file.removesuffix(".schema.json"))
        for part in [p for p in pointer.split("/") if p]:
            doc = doc[part]
        assert doc, ref


def test_lookup_makers() -> None:
    assert contracts.LOOKUP_MAKERS == ("bosch", "siemens", "miele", "aeg")


def test_models_are_pydantic() -> None:
    from contracts.models.card import Card

    assert issubclass(Card, BaseModel)


def test_step_ids_unique_per_card_across_jobs() -> None:
    import copy

    import contracts

    card = json.loads((EXAMPLES / "card" / "acme-wm-0000.json").read_text())
    assert contracts.card_invariants(card) == []
    contracts.load_card(card)
    two = copy.deepcopy(card)
    other = copy.deepcopy(two["jobs"][0])
    other["id"] = "second_job"
    other["steps"] = [copy.deepcopy(other["steps"][-1])]  # same step id, different job
    two["jobs"].append(other)
    dup = other["steps"][0]["id"]
    assert contracts.card_invariants(two) == [f"step id {dup!r} is used 2 times; step ids are unique per card"]
    with pytest.raises(contracts.ContractError, match="unique per card"):
        contracts.load_card(two)
    with pytest.raises(ValidationError, match="unique per card"):
        contracts.validate("card", two)


def _openapi_validator(name: str) -> Any:
    from jsonschema import Draft202012Validator

    comp = yaml.safe_load(contracts.OPENAPI.read_text())["components"]["schemas"]
    root = json.loads(json.dumps({"$defs": comp, **comp[name]}).replace("#/components/schemas/", "#/$defs/"))
    return Draft202012Validator(root)


def test_appliance_in_needs_model_or_plate() -> None:
    v = _openapi_validator("ApplianceIn")
    for ok in ({"model": "SMV4HAX40N"}, {"plate_photo_id": "p1"},
               {"model": "X", "pdf_url": "https://www.bosch-home.nl/x.pdf"}):
        assert v.is_valid(ok), ok
    for bad in ({}, {"alias": "vaatwasser", "kind": "dishwasher"}, {"model": None}, {"model": ""}):
        assert not v.is_valid(bad), bad


def test_appliance_ingest_notice() -> None:
    v = _openapi_validator("Appliance")
    base = {"id": "a", "alias": "x", "kind": "dishwasher", "brand": "bosch", "model": "m",
            "plate_confirmed": True, "card_status": "pending"}
    assert v.is_valid(base)
    assert v.is_valid({**base, "ingest": {"stage": "lookup", "notice": None}})
    assert v.is_valid({**base, "ingest": {"stage": "lookup", "notice": {
        "kind": "lookup_failed", "text": {"en": "No manual found.", "nl": "Geen handleiding gevonden."},
        "maker_page": None}}})
    assert not v.is_valid({**base, "ingest": {"stage": "brewing"}})
    assert not v.is_valid({**base, "ingest": {"stage": "done", "notice": {"kind": "model_said_so", "text": {}}}})
    post = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]["/appliances"]["post"]["responses"]
    assert {"201", "409", "422"} <= set(post)


def test_cite_args_one_shape() -> None:
    from contracts.models.tools import CiteArgs

    step = {"step_id": "s3", "page": None, "quote": None}
    manual = {"step_id": None, "page": 14, "quote": "Clean the filters regularly."}
    for ok in (step, manual):
        contracts.validate("tools", ok, "CiteArgs")
        assert contracts.cite_args_invariants(CiteArgs.model_validate(ok)) == []
    for bad in ({**step, "page": 14}, {**manual, "step_id": "s3"}, {**manual, "quote": None},
                {"step_id": None, "page": None, "quote": None}):
        with pytest.raises(contracts.ContractError):
            contracts.validate("tools", bad, "CiteArgs")
        assert contracts.cite_args_invariants(CiteArgs.model_validate(bad))
    with pytest.raises(ValidationError):
        contracts.validate("tools", {**manual, "quote": "x" * 301}, "CiteArgs")


def test_quote_may_carry_an_english_line() -> None:
    card = json.loads((Path(contracts.__file__).parent / "examples/card/acme-wm-0000.json").read_text())
    q = card["jobs"][0]["steps"][0]["quote"]
    q.update(text="Draai het grove filter linksom en neem het filtersysteem eruit.", lang="nl",
             en="Turn the coarse filter anticlockwise and take the filter system out.")
    contracts.validate("card", card)
    assert contracts.load_card(card).jobs[0].steps[0].quote.en == q["en"]
    q["en"] = "x" * 401
    with pytest.raises(ValidationError):
        contracts.validate("card", card)


def test_store_migration_3_keeps_pilot_events_and_adds_snooze() -> None:
    import re

    text = contracts.STORE_SQL.read_text()
    parts = re.split(r"^-- migration (\d+)\b.*$", text, flags=re.M)
    migrations = {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("pragma foreign_keys = on")
    db.executescript(migrations[1] + migrations[2])
    db.execute("insert into households (id, invite_code, created_at) values ('h1', 'c1', '2026-10-27T00:00:00Z')")
    db.execute("insert into pilot_events (household_id, kind, channel, at, reminder_key) "
               "values ('h1', 'reminder_sent', 'push', '2026-10-27T08:00:00Z', 'a:j:2026-10-27')")
    db.executescript(migrations[3])
    assert db.execute("pragma user_version").fetchone() == (3,)
    assert db.execute("select kind, reminder_key from pilot_events").fetchall() == [("reminder_sent", "a:j:2026-10-27")]
    db.execute("insert into pilot_events (household_id, kind, channel, at, reminder_key, snooze_until) values "
               "('h1', 'reminder_snoozed', 'app', '2026-10-27T09:00:00Z', 'a:j:2026-10-27', '2026-10-28T09:00:00Z')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into pilot_events (household_id, kind, at, snooze_until) "
                   "values ('h1', 'reminder_sent', '2026-10-27T09:00:00Z', '2026-10-28T09:00:00Z')")
    db.execute("delete from households where id = 'h1'")
    assert db.execute("select count(*) from pilot_events").fetchone() == (0,)


def test_store_migration_14_keeps_sessions_and_allows_ended() -> None:
    import re

    text = contracts.STORE_SQL.read_text()
    parts = re.split(r"^-- migration (\d+)\b.*$", text, flags=re.M)
    migrations = {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("pragma foreign_keys = on")
    db.executescript("".join(migrations[n] for n in range(1, 14)))
    db.execute("insert into households (id, invite_code, created_at) values ('h1', 'c1', '2026-10-27T00:00:00Z')")
    db.execute("insert into sessions (id, household_id, job_id, mode, started_at, ended_at, outcome, usd) values "
               "('s1', 'h1', 'j', 'api', '2026-10-27T08:00:00Z', '2026-10-27T08:05:00Z', 'abandoned', 0.25)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update sessions set outcome = 'ended'")
    db.executescript(migrations[14])
    assert db.execute("pragma user_version").fetchone() == (14,)
    assert db.execute("select id, outcome, usd from sessions").fetchall() == [("s1", "abandoned", 0.25)]
    db.execute("update sessions set outcome = 'ended'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update sessions set outcome = 'gone'")
    db.execute("delete from households where id = 'h1'")
    assert db.execute("select count(*) from sessions").fetchone() == (0,)


def test_store_migration_4_marks_test_households_never_own() -> None:
    import re

    parts = re.split(r"^-- migration (\d+)\b.*$", contracts.STORE_SQL.read_text(), flags=re.M)
    migrations = {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.executescript(migrations[1] + migrations[2] + migrations[3])
    db.execute("insert into households (id, invite_code, is_own, created_at) values ('own', 'c1', 1, '2026-10-27')")
    db.executescript(migrations[4])
    assert db.execute("pragma user_version").fetchone() == (4,)
    assert db.execute("select is_own, is_test from households").fetchall() == [(1, 0)]
    db.execute("insert into households (id, invite_code, is_test, created_at) values ('t', 'c2', 1, '2026-10-27')")
    for bad in ("(id, invite_code, is_own, is_test, created_at) values ('x', 'c3', 1, 1, '2026-10-27')",
                "(id, invite_code, is_test, created_at) values ('y', 'c4', 2, '2026-10-27')"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"insert into households {bad}")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update households set is_test = 1 where id = 'own'")


def test_due_matches_the_server() -> None:
    import typing
    from datetime import date

    from app.schedule.due import Due, Reason

    v = _openapi_validator("Due")
    assert v.schema["properties"]["reason"]["enum"] == list(typing.get_args(Reason))
    d = Due(household_id="h", appliance_id="a", alias="wasmachine", job_id="clean_debris_filter",
            title="Clean the debris filter", due_on=None, due_at=None, last_done=None, last_done_source=None,
            reason="unanswered", quote="Reinig het filter elke 2 maanden.", quote_lang="nl", page=43,
            duration_min=None, lang="en", every=2, unit="month", due_name="debris filter")
    assert v.is_valid(d.api()), list(v.iter_errors(d.api()))
    assert d.api()["last_done"]["source"] == "unknown"  # why the phone reads reason, not the source
    assert v.is_valid({**d.api(), "due_on": date(2026, 12, 1).isoformat(), "reason": "scheduled", "duration_min": 15})
    assert not v.is_valid({**d.api(), "reason": "overdue"})
    assert not v.is_valid({k: x for k, x in d.api().items() if k != "reason"})



def _migrations() -> dict[int, str]:
    import re

    parts = re.split(r"^-- migration (\d+)\b.*$", contracts.STORE_SQL.read_text(), flags=re.M)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def test_store_migration_5_people_schedule_and_changes() -> None:
    m = _migrations()
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("pragma foreign_keys = on")
    db.executescript(m[1] + m[2] + m[3] + m[4])
    db.execute("insert into households (id, invite_code, created_at) values ('h1', 'c1', '2026-10-27T00:00:00Z')")
    db.execute("insert into households (id, invite_code, created_at, deleted_at) values ('h2', 'c2', '2026', '2026')")
    db.execute("insert into appliances (id, household_id, alias, kind, brand, model, added_by, created_at) "
               "values ('a1', 'h1', 'washer', 'washer', 'acme', 'WM-0000', 'typed', '2026-10-27')")
    db.executescript(m[5])
    assert db.execute("pragma user_version").fetchone() == (5,)
    # every live household gets its first admin, whom the household token speaks for
    assert db.execute("select household_id, role, view_only from people").fetchall() == [("h1", "admin", 0)]
    (admin,) = db.execute("select id from people").fetchone()
    ok = [
        "insert into job_schedule (appliance_id, job_id, kind, every, unit, updated_at) "
        "values ('a1', 'j1', 'every', 4, 'month', 't')",
        "insert into job_schedule (appliance_id, job_id, kind, min, max, unit, updated_at) "
        "values ('a1', 'j2', 'window', 3, 6, 'month', 't')",
        "insert into job_schedule (appliance_id, job_id, snoozed_until, updated_at) "
        "values ('a1', 'j3', '2026-11-01', 't')",
        f"insert into household_tokens (id, household_id, by_kind, scope, person_id, label, token_sha256, created_at) "
        f"values ('t1', 'h1', 'app', 'write', '{admin}', 'phone', 's1', 't')",
        "insert into household_tokens (id, household_id, by_kind, scope, label, token_sha256, created_at) "
        "values ('t2', 'h1', 'agent', 'read', 'Claude desktop', 's2', 't')",
        "insert into job_change (household_id, appliance_id, job_id, at, kind, by_kind, by_token, by_label, "
        "before_json, after_json) "
        "values ('h1', 'a1', 'j1', 't', 'schedule', 'agent', 't2', 'Claude desktop', '{}', '{}')",
        "insert into job_change (household_id, appliance_id, job_id, at, kind, by_kind, by_person, by_label, "
        "before_json, after_json, undoes) values ('h1', 'a1', 'j1', 't', 'undo', 'app', "
        f"'{admin}', 'Admin', '{{}}', '{{}}', 1)",
        f"insert into person_alerts (person_id, items, quiet_from, quiet_to) "
        f"values ('{admin}', 'chosen', '22:00', '07:30')",
        f"insert into person_follows values ('{admin}', 'a1')",
    ]
    for sql in ok:
        db.execute(sql)
    bad = [
        "insert into job_schedule (appliance_id, job_id, kind, min, max, unit, updated_at) "
        "values ('a1', 'x1', 'window', 6, 3, 'month', 't')",                       # min >= max
        "insert into job_schedule (appliance_id, job_id, kind, every, unit, updated_at) "
        "values ('a1', 'x2', 'every', 30, 'cycle', 't')",                          # yours is a time, never cycles
        "insert into job_schedule (appliance_id, job_id, kind, every, min, max, unit, updated_at) "
        "values ('a1', 'x3', 'every', 4, 3, 6, 'month', 't')",                     # both shapes at once
        "insert into household_tokens (id, household_id, by_kind, scope, label, token_sha256, created_at) "
        "values ('t3', 'h1', 'feed', 'write', 'cal', 's3', 't')",                  # a feed token is read only
        "insert into household_tokens (id, household_id, by_kind, scope, label, token_sha256, created_at) "
        "values ('t4', 'h1', 'app', 'write', 'phone', 's4', 't')",                 # an app token is someone's
        "insert into job_change (household_id, appliance_id, job_id, at, kind, by_kind, by_label, before_json, "
        "after_json) values ('h1', 'a1', 'j1', 't', 'undo', 'app', 'x', '{}', '{}')",   # an undo names what it undoes
        "insert into job_change (household_id, appliance_id, job_id, at, kind, by_kind, by_label, before_json, "
        "after_json) values ('h1', 'a1', 'j1', 't', 'done', 'feed', 'x', '{}', '{}')",  # a feed never writes
        "insert into people (id, household_id, name, role, created_at) values ('p2', 'h1', 'Admin', 'member', 't')",
        "insert into people (id, household_id, name, role, view_only, created_at) "
        "values ('p3', 'h1', 'Sam', 'admin', 1, 't')",
        "insert into people (id, household_id, name, role, created_at) values ('p4', 'h1', 'Kim', 'viewer', 't')",
        f"insert into person_alerts (person_id, quiet_from) values ('{admin}', '22:00')",
    ]
    for sql in bad:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)
    db.execute("update job_change set undone_by = 2 where id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update job_change set undone_by = 1 where id = 2")             # an undo is never undone
    db.execute("delete from households where id = 'h1'")
    assert db.execute("select count(*) from job_change").fetchone() == (0,)


DUE_113 = {
    "appliance_id": "a1", "job_id": "clean_debris_filter", "title": "Clean the debris filter",
    "due_on": "2027-01-27", "last_done": {"date": "2026-10-27", "source": "user"},
    "interval_quote": "from your manual, p. 43", "page": 43, "reason": "scheduled",
    "quote": "Reinig het filter elke 2 maanden.", "duration_min": None,
    "manual": {"every": 2, "unit": "month", "page": 43, "quote": "Reinig het filter elke 2 maanden."},
    "schedule": {"kind": "window", "min": 3, "max": 6, "unit": "month"}, "window_end": "2027-04-27",
    "status": "upcoming", "snoozed_until": None, "assignee": None,
}


def test_due_113_shapes() -> None:
    v = _openapi_validator("Due")
    assert v.is_valid(DUE_113), list(v.iter_errors(DUE_113))
    for ok in ({"schedule": None, "window_end": None}, {"schedule": {"kind": "every", "every": 4, "unit": "month"},
               "window_end": None}, {"status": "none", "due_on": None, "reason": "unanswered"},
               {"manual": {"every": 30, "unit": "cycle", "page": 12, "quote": "q"}}):
        assert v.is_valid({**DUE_113, **ok}), ok
    for bad in ({"status": "snoozed"}, {"schedule": {"kind": "every", "every": 30, "unit": "cycle"}},
                {"schedule": {"kind": "window", "min": 3, "unit": "month"}}, {"schedule": {"kind": "manual"}}):
        assert not v.is_valid({**DUE_113, **bad}), bad
    for key in ("manual", "schedule", "window_end", "status", "snoozed_until", "assignee"):
        assert not v.is_valid({k: x for k, x in DUE_113.items() if k != key}), key
    s = _openapi_validator("ScheduleIn")
    assert s.is_valid({"kind": "manual"}) and s.is_valid({"kind": "every", "every": 4, "unit": "week"})
    assert not s.is_valid({"kind": "manual", "every": 4})


def test_change_and_people_shapes() -> None:
    state = {"last_done": "2026-10-27", "last_done_source": "user", "schedule": None, "snoozed_until": None,
             "assignee": None}
    change = {"id": 7, "at": "2026-10-27T12:00:00Z", "appliance_id": "a1", "job_id": "j1", "kind": "schedule",
              "by": {"kind": "agent", "person_id": None, "label": "Claude desktop"}, "before": state,
              "after": {**state, "schedule": {"kind": "every", "every": 4, "unit": "month"}}, "undone": False}
    v = _openapi_validator("Change")
    assert v.is_valid(change), list(v.iter_errors(change))
    assert v.is_valid({**change, "before": {**state, "last_done": None, "last_done_source": None}})  # unanswered
    for bad in ({"by": {"kind": "feed", "person_id": None, "label": "cal"}}, {"kind": "stop"},
                {"by": {"kind": "app", "label": "x"}}, {"by": {"kind": "app", "person_id": "p1", "label": ""}}):
        assert not v.is_valid({**change, **bad}), bad
    a = _openapi_validator("Alerts")
    alerts = {"push": True, "calendar": True, "hub": False, "items": "chosen", "follows": ["a1"],
              "quiet": {"from": "22:00", "to": "07:30"}}
    assert a.is_valid(alerts) and a.is_valid({**alerts, "quiet": None})
    assert not a.is_valid({**alerts, "quiet": {"from": "24:00", "to": "07:00"}})
    person = _openapi_validator("Person")
    assert person.is_valid({"id": "p1", "name": "Sam", "role": "member", "view_only": True, "me": True})
    for role in ("owner", "viewer", "guest"):
        assert not person.is_valid({"id": "p1", "name": "Sam", "role": role, "view_only": False, "me": True}), role
    i = _openapi_validator("Invite")
    inv = {"code": "c", "join_url": "https://fixit.example.org/#join/c", "role": "member", "view_only": False,
           "email": None, "expires_at": "2026-11-03T00:00:00Z"}
    assert i.is_valid(inv)
    for url in ("u", "https://fixit.example.org/join/c", "https://fixit.example.org/?code=c#join/c",
                "https://fixit.example.org/#join/"):                   # the code rides in the fragment only
        assert not i.is_valid({**inv, "join_url": url}), url
    t = _openapi_validator("Token")
    assert not t.is_valid({"id": "t", "by_kind": "app", "scope": "write", "label": "x", "created_at": "t",
                           "last_used_at": None})   # app tokens come from join, not from /household/tokens


def test_mcp_tools_only_reach_the_schedule() -> None:
    api = yaml.safe_load(contracts.OPENAPI.read_text())
    tools = json.loads(contracts.MCP_TOOLS.read_text())["tools"]
    assert [t["name"] for t in tools] == ["whats_due", "last_done", "what_does_the_manual_say", "mark_done", "snooze",
                                          "set_schedule", "undo_my_change", "start_repair"]
    for t in tools:
        assert t["method"].lower() in api["paths"][t["route"]], t
        assert t["write"] == (t["method"] != "GET"), t
        assert not any(w in t["route"] for w in ("/ws", "/sessions", "/household/", "/me/")), t
        # an agent undoes only what its own token did, never a person's change
        assert ("undo" in t["route"]) == (t.get("body") == {"own": True}), t
    undo = api["paths"]["/appliances/{appliance_id}/jobs/{job_id}/undo"]["post"]
    body = undo["requestBody"]["content"]["application/json"]["schema"]
    assert undo["requestBody"]["required"] is False
    from jsonschema import Draft202012Validator
    v = Draft202012Validator(body)
    assert v.is_valid({}) and v.is_valid({"own": True}) and v.is_valid({"own": False})
    assert not v.is_valid({"own": "yes"}) and not v.is_valid({"own": True, "by_token": "t"})
    assert "not_own" in undo["responses"]["409"]["description"] and "422" in undo["responses"]


def test_ingest_progress_and_manual_upload() -> None:
    v = _openapi_validator("Ingest")
    full = {"stage": "ingest", "manual": {"pages": 64, "lang": "nl", "title": None}, "eta_s": 70,
            "quotes": {"checked": 40, "passed": 39},
            "progress": 0.4, "elapsed_s": 31,
            "jobs_ready": [{"id": "clean_filters", "title": {"en": "Clean the filters"}, "steps": 8, "page": 42,
                            "duration_min": 15, "kind": "upkeep", "startable": False}],
            "jobs_refused": [{"id": "replace_heater", "reason": "mains"}]}
    assert v.is_valid({"stage": "fetch"}) and v.is_valid(full), list(v.iter_errors(full))
    for bad in ({"manual": {"pages": 0, "lang": "nl"}}, {"eta_s": -1}, {"quotes": {"checked": 1}},
                {"jobs_ready": [{"id": "x", "title": {}, "steps": 1, "page": 1}]},
                {"jobs_ready": [{**full["jobs_ready"][0], "steps": 0}]}, {"progress": 1.5}, {"stage": "read"},
                {"jobs_ready": [{**full["jobs_ready"][0], "kind": "repair"}]},
                {"jobs_refused": [{"id": "x", "reason": "gas", "title": {"en": "x"}}]}):
        assert not v.is_valid({**full, **bad}), bad
    post = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]["/appliances/{appliance_id}/manual"]["post"]
    assert set(post["requestBody"]["content"]) == {"application/pdf"}
    pdf = post["requestBody"]["content"]["application/pdf"]
    assert pdf["x-max-bytes"] == pdf["schema"]["maxLength"] == 50 * 1024 * 1024
    n = _openapi_validator("IngestNotice")
    assert n.is_valid({"kind": "lookup_failed", "text": {"en": "x"}, "reason": "site_blocked"})
    assert not n.is_valid({"kind": "lookup_failed", "text": {"en": "x"}, "reason": "the site said no"})
    assert {"201", "403", "404", "413", "415"} <= set(post["responses"])


def test_store_migration_6_judges_and_return_links() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(contracts.STORE_SQL.read_text())
    now = "2026-10-29T09:00:00Z"
    db.execute("INSERT INTO invite_links VALUES ('l1', 'h1', 'judges', 'Judges', 0.5, 20, 40, ?, NULL, ?)", (now, now))
    for bad in ("'l2', 'h2', 'pilot', 'x', 0.5, 20, 40", "'l2', 'h1', 'judges', 'x', 0.5, 20, 40",
                "'l2', 'h2', 'judges', '', 0.5, 20, 40", "'l2', 'h2', 'judges', 'x', 0.5, NULL, 40",
                "'l2', 'h2', 'judges', 'x', 0.5, 20, 0", "'l2', 'h2', 'judges', 'x', 0.5, 20, NULL"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"INSERT INTO invite_links VALUES ({bad}, ?, NULL, ?)", (now, now))
    db.execute("INSERT INTO households (id, invite_code, created_at, invite_link_id, joined_via) "
               "VALUES ('hh1', 'random', ?, 'l1', 'link')", (now,))
    db.execute("INSERT INTO households (id, invite_code, created_at, joined_via) VALUES ('hh2', 'r2', ?, 'door')",
               (now,))
    db.execute("INSERT INTO households (id, invite_code, created_at) VALUES ('hh3', 'r3', ?)", (now,))
    assert db.execute("SELECT joined_via FROM households WHERE id = 'hh3'").fetchone() == ("invite",)
    for bad in ("'hh4', 'r4', ?, NULL, 'link'", "'hh4', 'r4', ?, 'l1', 'door'", "'hh4', 'r4', ?, NULL, 'qr'"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO households (id, invite_code, created_at, invite_link_id, joined_via) "
                       f"VALUES ({bad})", (now,))
    db.execute("INSERT INTO return_codes (code_sha256, household_id, created_at, expires_at, used_at) "
               "VALUES ('r1', 'hh1', ?, ?, NULL)", (now, now))
    db.execute("UPDATE households SET name = 'Demo home' WHERE id = 'hh1'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE households SET name = '' WHERE id = 'hh1'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO return_codes (code_sha256, household_id, created_at, expires_at, used_at) "
                   "VALUES ('r1', 'hh1', ?, ?, NULL)", (now, now))


def test_open_door_try_return_and_cap_shapes() -> None:
    from jsonschema import Draft202012Validator

    api = yaml.safe_load(contracts.OPENAPI.read_text())
    body = Draft202012Validator(
        api["paths"]["/households"]["post"]["requestBody"]["content"]["application/json"]["schema"])
    assert body.is_valid({"invite_code": "abc", "consent": True})
    assert body.is_valid({"consent": True, "age_ok": True, "lang": "en"})
    assert not body.is_valid({"consent": True})                      # the open door asks for the age line
    try_ = Draft202012Validator(
        api["paths"]["/households/try"]["post"]["requestBody"]["content"]["application/json"]["schema"])
    assert try_.is_valid({"code": "k", "consent": True, "age_ok": True})
    assert not try_.is_valid({"code": "k", "consent": True, "age_ok": False})
    cap = _openapi_validator("CapError")
    assert cap.is_valid({"error": "judges_cap", "reopens_at": None})        # a total, ever: it never reopens
    assert not cap.is_valid({"error": "cap_reached", "reopens_at": "2026-10-30T00:00:00Z"})
    assert cap.is_valid({"error": "judges_full", "reopens_at": None})
    assert not cap.is_valid({"error": "judges_full", "reopens_at": "2026-10-30T00:00:00Z"})
    assert not cap.is_valid({"error": "judges_cap", "reopens_at": "2026-10-30T00:00:00Z"})
    for daily in ("door_cap", "address_cap"):                              # a daily cap reopens at UTC midnight
        assert cap.is_valid({"error": daily, "reopens_at": "2026-10-30T00:00:00Z"})
        assert not cap.is_valid({"error": daily, "reopens_at": None})
    assert not cap.is_valid({"error": "home_cap", "reopens_at": None})
    assert cap.is_valid({"error": "global_cap", "reopens_at": None})       # a one-off credit pool has no time
    assert cap.is_valid({"error": "global_cap", "reopens_at": "2026-10-30T00:00:00Z"})
    ws = contracts.validator("ws", "CapClose")
    assert ws.is_valid({"error": "home_cap", "reopens_at": "2026-10-30T00:00:00Z"})
    assert ws.is_valid({"error": "global_cap", "reopens_at": None})
    assert not ws.is_valid({"error": "home_cap", "reopens_at": None})
    ws_enum = json.loads(contracts.schema_path("ws").read_text())["$defs"]["CapClose"]["properties"]["error"]["enum"]
    rest_enum = api["components"]["schemas"]["CapError"]["properties"]["error"]["enum"]
    # a full link, and the door's daily limits, only ever answer opening a household, never a session
    assert set(ws_enum) == set(rest_enum) - {"judges_full", "door_cap", "address_cap"}
    assert ws.is_valid({"error": "judges_cap", "reopens_at": None})
    assert not ws.is_valid({"error": "judges_cap", "reopens_at": "2026-10-30T00:00:00Z"})
    assert "429" in api["paths"]["/households/try"]["post"]["responses"]
    hh = _openapi_validator("Household")
    assert hh.is_valid({"id": "h", "country": "nl", "lang": "nl", "name": None, "pilot": False})
    assert not hh.is_valid({"id": "h", "country": "nl", "lang": "nl", "name": "x" * 41, "pilot": False})


def test_ledger_provider_is_optional_and_kept(tmp_path: Path) -> None:
    from calls.ledger import Ledger

    (tmp_path / "budgets.toml").write_text("total_cap = 1.0\n[lanes]\nl7 = 1.0\n")
    ledger = Ledger(tmp_path / "ledger.jsonl", tmp_path / "budgets.toml")
    base = {"model": "muse-spark-open", "response_model": "muse-spark-open", "status": "completed", "usd": 0.001,
            "error": None, "lane": "l7", "household": None, "session_id": None, "mode": "partner"}
    old = ledger.append(base)                        # a line from before the field: provider null, read as meta
    new = ledger.append({**base, "provider": "fireworks"})
    assert old["provider"] is None and new["provider"] == "fireworks"
    for line in (old, new, {k: v for k, v in old.items() if k != "provider"}):
        contracts.validate("ledger", line)
    with pytest.raises(ValidationError):
        contracts.validate("ledger", {**new, "provider": "openai"})


def test_due_without_a_manual_interval_carries_the_households_own() -> None:
    """An upkeep job whose manual gives no interval is on /due only with a person's own schedule, and cites nothing."""
    v = _openapi_validator("Due")
    own = {**DUE_113, "manual": None, "interval_quote": None, "quote": None, "page": None,
           "schedule": {"kind": "every", "every": 6, "unit": "month"}, "window_end": None}
    assert v.is_valid(own), list(v.iter_errors(own))
    for bad in ({"schedule": None}, {"quote": "Reinig het filter."}, {"interval_quote": "from your manual, p. 43"},
                {"page": 43}):
        assert not v.is_valid({**own, **bad}), bad
    for bad in ({"quote": None}, {"interval_quote": None}):     # a manual interval is always cited
        assert not v.is_valid({**DUE_113, **bad}), bad
    paths = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]
    put = paths["/appliances/{appliance_id}/jobs/{job_id}/schedule"]["put"]
    assert {"204", "403", "404", "422"} <= set(put["responses"])
    assert "person_only" in put["responses"]["403"]["description"]
    assert "fix_has_no_schedule" in put["responses"]["422"]["description"]
    assert "204" in paths["/appliances/{appliance_id}/jobs/{job_id}/undo"]["post"]["responses"]


def _troubleshooting_card() -> dict[str, Any]:
    import copy

    card = json.loads((EXAMPLES / "card" / "acme-wm-0000.json").read_text())
    fix = copy.deepcopy(card["jobs"][0])
    fix["id"], fix["kind"] = "clear_drain_pump", "fix"
    for i, step in enumerate(fix["steps"]):
        step["id"] = f"pump_{i}"
    card["jobs"].append(fix)
    card["troubleshooting"] = [{
        "id": "e21", "codes": ["E21"], "indicator": None, "symbol": {"page": 4, "bbox": [0.1, 0.2, 0.3, 0.25]},
        "symptom": {"en": "Water stays in the drum."}, "meaning": {"en": "The pump cannot drain."},
        "cause": {"en": "The debris filter is blocked."}, "remedy": {"en": "Clean the debris filter."},
        "remedy_kind": "user", "escalate_to": None, "job_ref": "clear_drain_pump",
        "prevented_by": [{"job_id": "clean_debris_filter", "quote_ref": "q_interval"}],
        "repeat": {"count": 2, "days": 30, "quote_ref": "q_interval"}, "page": 4, "quote_ref": "q_arrows",
    }]
    return card


def test_troubleshooting_rows_are_checked_and_cite_the_card() -> None:
    import copy

    card = _troubleshooting_card()
    contracts.validate("card", card)
    assert contracts.card_invariants(card) == []
    row = card["troubleshooting"][0]
    for bad in (
        {"codes": [], "indicator": None, "symptom": None},                  # says nothing a person could see
        {"remedy_kind": "technician", "escalate_to": None},                 # a call carries no fix job
        {"remedy_kind": "technician", "escalate_to": "technician", "job_ref": None},
        {"remedy_kind": "installer", "escalate_to": None, "job_ref": None, "landlord": True},
        {"escalate_to": "landlord"},
        {"symbol": {"page": 4, "bbox": [0.1, 0.2, 1.3, 0.25]}},             # bbox is page fractions
        {"codes": ["E" * 13]}, {"remedy": None}, {"repeat": {"count": 1, "days": 30, "quote_ref": "q_interval"}},
    ):
        two = copy.deepcopy(card)
        two["troubleshooting"][0] = {**row, **bad}
        with pytest.raises(ValidationError):
            contracts.validate("card", two)
    ok = {**row, "remedy_kind": "technician", "escalate_to": None, "job_ref": None, "codes": [],
          "indicator": {"en": "The tap light flashes."}}
    contracts.validate("card", {**card, "troubleshooting": [ok]})
    broken = copy.deepcopy(card)
    broken["troubleshooting"] = [{**row, "quote_ref": "q_nope", "job_ref": "clean_debris_filter",
                                  "prevented_by": [{"job_id": "clear_drain_pump", "quote_ref": "q_interval"}]},
                                 {**row, "repeat": {"count": 2, "days": 1, "quote_ref": "q_gone"}}]
    assert contracts.card_invariants(broken) == [
        "troubleshooting id 'e21' is used 2 times",
        "troubleshooting 'e21': quote_ref 'q_nope' is not in quotes",
        "troubleshooting 'e21': job_ref 'clean_debris_filter' is not a fix job on this card",
        "troubleshooting 'e21': prevented_by 'clear_drain_pump' is not an upkeep job on this card",
        "troubleshooting 'e21': quote_ref 'q_gone' is not in quotes",
    ]
    with pytest.raises(contracts.ContractError):
        contracts.load_card(broken)


def test_fault_meaning_answers_only_from_the_card() -> None:
    v = _openapi_validator("FaultMeaning")
    hit = {"code": "e 21", "matches": [{"code": "E21", "job_id": "clear_drain_pump", "title": {"en": "Clear the pump"},
                                        "page": 4, "quote": "Screw the filter back in clockwise.",
                                        "unconfirmed": False}],
           "candidates": [], "refused": [], "table_page": 4, "notice": None, "landlord": False}
    miss = {"code": "E2O", "matches": [], "candidates": ["E20", "E21"], "refused": [], "table_page": 4,
            "notice": "Geen code E2O in de handleiding.", "landlord": False}
    for ok in (hit, miss, {**miss, "table_page": None}):
        assert v.is_valid(ok), list(v.iter_errors(ok))
    match = hit["matches"][0]
    for bad in ({"code": ""}, {"code": "x" * 21}, {"candidates": ["E1", "E2", "E3", "E4", "E5", "E6"]},
                {"matches": [{**match, "page": 0}]}, {"matches": [{**match, "quote": ""}]},
                {"matches": [{**match, "why": "the model thinks"}]}, {"notice": 3}):
        assert not v.is_valid({**hit, **bad}), bad
    get = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]["/appliances/{appliance_id}/meaning"]["get"]
    assert {"200", "404", "422"} <= set(get["responses"]) and "post" not in get
    assert get["parameters"][0]["schema"] == {"type": "string", "minLength": 1, "maxLength": 20}


def test_fault_meaning_row_matches_and_the_camera_read() -> None:
    v = _openapi_validator("FaultMeaning")
    row = {"code": "E21", "job_id": "clear_drain_pump", "title": {"en": "Clear the pump"}, "page": 4,
           "quote": "Screw the filter back in clockwise.", "row_id": "e21", "meaning": {"en": "The pump cannot drain."},
           "cause": None, "remedy": {"en": "Clean the debris filter."}, "remedy_kind": "user",
           "escalate_to": "technician", "symbol": {"page": 4, "bbox": [0.1, 0.2, 0.3, 0.25]},
           "unconfirmed": False}
    base = {"code": "E21", "matches": [row], "candidates": [], "refused": [], "table_page": 4, "notice": None,
            "landlord": True}
    call = {**row, "remedy_kind": "technician", "escalate_to": None, "job_id": None}
    for ok in (base, {**base, "matches": [call]}, {**base, "matches": [{**row, "unconfirmed": True}]},
               {**base, "read": {"code": "E21", "row_id": None, "uncertain": False}},
               {**base, "read": {"code": None, "row_id": None, "uncertain": True}}):
        assert v.is_valid(ok), list(v.iter_errors(ok))
    job_only = {k: row[k] for k in ("code", "job_id", "title", "page", "quote", "unconfirmed")}
    for bad in (
        {"landlord": None},
        {"matches": [{k: x for k, x in row.items() if k != "remedy"}]},          # a row match carries the whole row
        {"matches": [{**job_only, "remedy_kind": "user"}]},                     # a job match carries none of it
        {"matches": [{**job_only, "unconfirmed": True}]},                       # only a row's code is ever drawn
        {"matches": [{k: x for k, x in row.items() if k != "unconfirmed"}]},
        {"matches": [{**call, "job_id": "clear_drain_pump"}]},                  # a call has no fix job
        {"matches": [{**call, "escalate_to": "installer"}]},
        {"matches": [{**row, "symbol": {"page": 4, "bbox": [0, 0, 2, 1]}}]},
        {"read": {"code": None, "row_id": None, "uncertain": False}},           # read nothing, so it is unsure
        {"read": {"code": "E21", "row_id": None}},
    ):
        assert not v.is_valid({**base, **bad}), bad
    paths = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]
    get = paths["/appliances/{appliance_id}/meaning"]["get"]["responses"]["200"]["content"]["application/json"]
    post = paths["/appliances/{appliance_id}/display"]["post"]
    assert {"not": {"required": ["read"]}, "properties": {"code": {"type": "string"}}} in get["schema"]["allOf"]
    assert {"required": ["read"]} in post["responses"]["200"]["content"]["application/json"]["schema"]["allOf"]
    jpeg = post["requestBody"]["content"]["image/jpeg"]
    assert jpeg["x-max-bytes"] == jpeg["schema"]["maxLength"] == 8 * 1024 * 1024
    assert {"200", "403", "404", "413", "415", "429"} <= set(post["responses"])
    assert "CapError" in post["responses"]["429"]["content"]["application/json"]["schema"]["$ref"]


def test_card_manual_says_where_it_came_from_and_which_models_it_covers() -> None:
    import copy

    card = json.loads((EXAMPLES / "card" / "acme-wm-0000.json").read_text())
    contracts.validate("card", card)                                   # a card from before 1.23 still loads
    sha = "a" * 64
    fetched = {**card["manual"], "source": "maker_site", "source_url": "https://www.example.com/wm-0000.pdf",
               "pdf_sha256": sha, "models": ["WM-0000", "WM-00*"], "models_page": 2}
    upload = {**fetched, "source": "household_upload", "source_url": f"urn:fixit:upload:{sha}", "models": []}
    del upload["models_page"]
    card["refused"] = [{"id": "replace_heater", "title": {"en": "Replace the heater"}, "reason": "mains", "page": 5}]
    for manual in (fetched, upload):
        contracts.validate("card", {**card, "manual": manual})
    bad = [
        {**fetched, "source_url": "file:///home/x/manual.pdf"},       # a maker_site card cites the maker's URL
        {**upload, "source_url": "https://www.example.com/wm-0000.pdf"},
        {**upload, "source_url": "file:///home/x/manual.pdf"},        # an upload never names a local path
        {k: v for k, v in fetched.items() if k != "pdf_sha256"},
        {k: v for k, v in fetched.items() if k != "models"},
        {k: v for k, v in fetched.items() if k != "models_page"},
        {**fetched, "models": ["x" * 41]}, {**fetched, "models": [f"M{i}" for i in range(61)]},
    ]
    for manual in bad:
        with pytest.raises(ValidationError):
            contracts.validate("card", {**card, "manual": manual})
    two = copy.deepcopy(card)
    two["refused"][0]["reason"] = "landlord"
    with pytest.raises(ValidationError):
        contracts.validate("card", two)


def test_a_light_has_no_code_and_the_household_says_whether_it_rents() -> None:
    v = _openapi_validator("FaultMeaning")
    row = {"code": None, "job_id": None, "title": {"en": "The tap light"}, "page": 4, "quote": "The tap light flashes.",
           "row_id": "tap_light", "meaning": {"en": "Water is off."}, "cause": None, "remedy": {"en": "Open the tap."},
           "remedy_kind": "user", "escalate_to": None, "symbol": None, "unconfirmed": False}
    light = {"code": None, "matches": [row], "candidates": [], "refused": [], "table_page": 4, "notice": None,
             "landlord": False, "read": {"code": None, "row_id": "tap_light", "uncertain": False}}
    assert v.is_valid(light), list(v.iter_errors(light))
    job = {"code": None, "job_id": "j", "title": {"en": "t"}, "page": 1, "quote": "q", "unconfirmed": False}
    for bad in ({"read": {"code": "E21", "row_id": None, "uncertain": False}},    # a read code is echoed
                {"matches": [job]}):                                             # only an indicator row lacks a code
        assert not v.is_valid({**light, **bad}), bad
    paths = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]
    get = paths["/appliances/{appliance_id}/meaning"]["get"]["responses"]["200"]["content"]["application/json"]
    assert {"not": {"required": ["read"]}, "properties": {"code": {"type": "string"}}} in get["schema"]["allOf"]
    hh = _openapi_validator("Household")
    for renter in (True, False, None):
        assert hh.is_valid({"id": "h", "country": "nl", "lang": "nl", "renter": renter, "pilot": True})
    patch = _openapi_validator("HouseholdPatch")
    assert patch.is_valid({"renter": True}) and not patch.is_valid({}) and not patch.is_valid({"name": "x"})
    assert "403" in paths["/household"]["patch"]["responses"]
    db = sqlite3.connect(":memory:")
    db.executescript(contracts.STORE_SQL.read_text())
    db.execute("insert into households (id, country, lang, invite_code, created_at) values ('h', 'nl', 'nl', 'c', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update households set renter = 2")


def test_ledger_origin_marks_public_sessions_only(tmp_path: Path) -> None:
    from calls.ledger import Ledger

    (tmp_path / "budgets.toml").write_text("total_cap = 1.0\n[lanes]\nl7 = 1.0\n")
    ledger = Ledger(tmp_path / "ledger.jsonl", tmp_path / "budgets.toml")
    base = {"model": "muse-spark-open", "response_model": "muse-spark-open", "status": "completed", "usd": 0.001,
            "error": None, "lane": "l7", "household": None, "session_id": None, "mode": "partner"}
    old, door = ledger.append(base), ledger.append({**base, "origin": "door"})
    assert old["origin"] is None and door["origin"] == "door"
    for line in (old, door, {**door, "origin": "judges"}):
        contracts.validate("ledger", line)
    with pytest.raises(ValidationError):
        contracts.validate("ledger", {**door, "origin": "invite"})
SHA = "b" * 64


def test_the_card_record_says_how_a_card_was_checked() -> None:
    v = _openapi_validator("CardRecord")
    rec = {"card_sha256": SHA, "version": 1, "built_at": "2026-10-27T10:00:00Z", "shared": True,
           "private_reason": None, "reviewed": {"by_person": False, "at": None}, "homes": 3,
           "quotes": {"checked": 90, "passed": 83}, "reports_open": {"total": 1, "safety": 1},
           "unconfirmed_codes": 0, "unaccepted_steps": 0}
    private = {**rec, "shared": False, "private_reason": "model_not_in_list", "homes": 1}
    conflict = {**private, "private_reason": "version_conflict"}
    assert v.is_valid({**private, "private_reason": "codes_unconfirmed", "unconfirmed_codes": 2})
    assert v.is_valid({**private, "private_reason": "steps_unaccepted", "unaccepted_steps": 1})
    for ok in (rec, private, conflict, {**rec, "reviewed": {"by_person": True, "at": "2026-10-27"}}):
        assert v.is_valid(ok), list(v.iter_errors(ok))
    for bad in ({"shared": False}, {"private_reason": "uploaded"}, {"private_reason": "samsung", "shared": False},
                {"reviewed": {"by_person": True, "at": None}}, {"homes": 0}, {"card_path": "cards/bosch/x.yaml"},
                {"reports_open": {"total": 1}}, {"why": "the model says so"},
                {"unconfirmed_codes": 1}, {"unconfirmed_codes": -1}, {"unaccepted_steps": 1}):  # a shared card has none
        assert not v.is_valid({**rec, **bad}), bad
    assert not v.is_valid({k: x for k, x in rec.items() if k != "unaccepted_steps"})
    notice = _openapi_validator("IngestNotice")
    for reason in ("maker_terms", "not_mine"):
        assert notice.is_valid({"kind": "lookup_failed", "text": {"en": "x"}, "reason": reason})
    paths = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]
    assert "404" in paths["/appliances/{appliance_id}/card/record"]["get"]["responses"]
    assert "/devices" not in paths                    # the Devices screen reads today's routes


def test_a_report_never_edits_a_card_and_not_mine_unlinks() -> None:
    v = _openapi_validator("ReportIn")
    for ok in ({"kind": "unsafe", "step_id": "s1", "note": "Mijn hand zat bij de pomp."},
               {"kind": "wrong_page", "job_id": "j", "page": 43, "note": "The interval is on p. 43."},
               {"kind": "wrong_step", "step_id": "s1"}, {"kind": "other", "note": "x"}, {"kind": "unsafe"}):
        assert v.is_valid(ok), ok
    for bad in ({"kind": "wrong_step", "note": "x"}, {"kind": "other"}, {"kind": "wrong_appliance", "note": "x"},
                {"kind": "wrong_page", "note": "x", "page": 0},
                {"kind": "wrong_page", "note": "x"}, {"kind": "unsafe", "note": "x" * 501},
                {"kind": "other", "note": "x", "card": {"jobs": []}}):
        assert not v.is_valid(bad), bad
    report = _openapi_validator("Report")
    by = {"kind": "app", "person_id": None, "label": "Admin"}
    filed = {"id": "r1", "appliance_id": "a1", "kind": "wrong_appliance", "job_id": None, "step_id": None,
             "note": None, "page": None, "card_sha256": SHA, "status": "open", "resolution": None,
             "created_at": "2026-10-27T10:00:00Z", "closed_at": None, "by": by}
    closed = {**filed, "status": "closed", "resolution": "Fixed in a new card version.",
              "closed_at": "2026-10-28T10:00:00Z"}
    assert report.is_valid(filed) and report.is_valid(closed)
    assert not report.is_valid({**closed, "resolution": None}) and not report.is_valid({**filed, "closed_at": "x"})
    paths = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]
    assert {"201", "403", "404", "409", "422"} <= set(paths["/appliances/{appliance_id}/reports"]["post"]["responses"])
    not_mine = paths["/appliances/{appliance_id}/not-mine"]["post"]["responses"]
    assert {"200", "403", "404", "409", "429"} <= set(not_mine) and "viewer" in not_mine["403"]["description"]
    assert "too_many_reports" in paths["/appliances/{appliance_id}/reports"]["post"]["responses"]["429"]["description"]
    assert "get" in paths["/reports"]
    db = sqlite3.connect(":memory:")
    db.executescript(contracts.STORE_SQL.read_text())
    db.execute("pragma foreign_keys = on")
    db.execute("insert into households (id, country, lang, invite_code, created_at) values ('h', 'nl', 'nl', 'c', 'x')")
    lib = "insert into card_library values (?, 'bosch', 'SMV4HAX40N', ?, ?, ?, ?, ?, 'h', 't', ?, ?)"
    db.execute(lib, ("cards/bosch/a.yaml", SHA, "maker_site", "exact", 1, None, None, None))
    db.execute(lib, ("p/up.yaml", SHA, "household_upload", "exact", 0, "uploaded", None, None))
    db.execute(lib, ("cards/bosch/b.yaml", SHA, "maker_site", "none", 1, None, "Reviewer", "t"))
    for i, reason in enumerate(("codes_unconfirmed", "steps_unaccepted")):       # private until a person checks
        db.execute(lib, (f"cards/bosch/w{i}.yaml", SHA, "maker_site", "exact", 0, reason, None, None))
    for i, bad in enumerate((("household_upload", "exact", 1, None, None, None),       # an upload is never shared
                             ("maker_site", "none", 1, None, None, None),              # nor a card no list covers
                             ("maker_site", "exact", 0, None, None, None),             # private says why
                             ("household_upload", "exact", 0, "no_model_list", None, None),
                             ("maker_site", "exact", 1, None, "Reviewer", "t"),        # review links a none card only
                             ("maker_site", "none", 0, "samsung", None, None))):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(lib, (f"x{i}", SHA, *bad))
    row = ("r1", "h", None, "cards/bosch/a.yaml", SHA, "wrong_appliance", None, None, None, None, None, "Admin",
           "open", None, "t", None)
    insert = "insert into card_reports values (" + ", ".join("?" * len(row)) + ")"
    db.execute(insert, row)
    for i, change in enumerate(({5: "other"}, {5: "wrong_step", 8: "x"}, {12: "closed", 15: "t"}, {9: 0}, {7: "s1"},
                                {3: "cards/none.yaml"})):
        bad = list(row)
        bad[0] = f"r{i + 2}"
        for k, x in change.items():
            bad[k] = x
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(insert, bad)


def test_a_fixit_step_only_undoes_an_earlier_power_off_step() -> None:
    import copy

    card = json.loads((EXAMPLES / "card" / "acme-wm-0000.json").read_text())
    job = card["jobs"][0]
    off = next(s for s in job["steps"] if s["power"] == "off")
    plug = {"id": "plug_back_in", "text": {"en": "Plug it back in."}, "origin": "fixit", "restores": off["id"],
            "page": None, "quote": None, "done_when": [], "parts": [], "power": "any", "zones": [], "view": "front",
            "look_only_lines": []}
    ok = copy.deepcopy(card)
    ok["jobs"][0]["steps"].append(plug)
    contracts.validate("card", ok)
    assert contracts.card_invariants(ok) == []
    by_ingest = copy.deepcopy(ok)
    by_ingest["jobs"][0]["steps"][-1]["added_by"] = "ingest"
    contracts.validate("card", by_ingest)
    # the same step may instead put back a part the power-off step took out, or turn the water back on: one step
    # each, under the same rules (no parts list on ours, so the part is named only in its words)
    for words in ("Put the debris filter back in place.", "Turn the water tap back on."):
        other = copy.deepcopy(card)
        other["jobs"][0]["steps"].append({**plug, "id": "restore", "added_by": "ingest", "text": {"en": words}})
        contracts.validate("card", other)
        assert contracts.card_invariants(other) == []
    for bad in ({"page": 4}, {"added_by": "model"}, {"quote": job["steps"][0]["quote"]}, {"power": "off"},
                {"parts": ["debris_filter"]},
                {"done_when": job["steps"][0]["done_when"]},
                {"witness": {"part": "x", "expect": "present", "anchor": None}},
                {"origin": "manual"}, {"restores": None}):
        two = copy.deepcopy(card)
        two["jobs"][0]["steps"].append({**plug, **bad})
        with pytest.raises(ValidationError):
            contracts.validate("card", two)
    early = copy.deepcopy(card)
    early["jobs"][0]["steps"].insert(0, {**plug, "restores": "nope"})
    manual = copy.deepcopy(card)
    manual["jobs"][0]["steps"][0]["added_by"] = "person"                 # only our own step says who added it
    with pytest.raises(ValidationError):
        contracts.validate("card", manual)
    twice = copy.deepcopy(ok)
    twice["jobs"][0]["steps"].append({**plug, "id": "plug_again"})
    assert contracts.card_invariants(early) == [
        "step 'plug_back_in': restores 'nope', which is not an earlier power-off step of its job",
        "step 'plug_back_in': a fixit step comes after every power-off step of its job",
    ]
    assert contracts.card_invariants(twice) == [f"job {job['id']!r}: step {off['id']!r} is restored 2 times"]
    with pytest.raises(contracts.ContractError):
        contracts.load_card(early)


def test_the_step_message_marks_our_own_step() -> None:
    base = {"type": "step", "seq": 1, "t_ms": 0, "job_id": "j", "step_id": "plug_back_in", "index": 3, "of": 4,
            "text": "Steek de stekker weer in het stopcontact.", "view": "front", "power": "any"}
    contracts.validate("ws", {**base, "origin": "fixit"})                # no page: the phone types it as optional
    contracts.validate("ws", {**base, "page": 4, "quote": "Reinig het filter."})
    for bad in ({}, {"page": None}, {"page": 4, "origin": "fixit"}, {"origin": "fixit", "quote": "Reinig het filter."}):
        with pytest.raises(ValidationError):
            contracts.validate("ws", {**base, **bad})


def test_a_person_ends_our_own_step_without_a_judge() -> None:
    base = {"result": "done", "clause_ids": [], "latency_ms": 0}
    from jsonschema import Draft202012Validator

    judge = json.loads((contracts.OPENAPI.parent / "judge.schema.json").read_text())
    v = Draft202012Validator({"$defs": judge["$defs"], "$ref": "#/$defs/Verdict"})
    v.validate({**base, "witnesses": {"sam": "none", "by_person": True}})
    v.validate({**base, "witnesses": {"judge": "done", "sam": "frame"}})
    for bad in ({"witnesses": {"sam": "frame"}},                                       # no judge, no person
                {"witnesses": {"judge": "done", "sam": "none", "by_person": True}},
                {"witnesses": {"sam": "frame", "by_person": True}},
                {"witnesses": {"sam": "none", "by_person": False}},
                {"witnesses": {"sam": "none", "by_person": True}, "result": "not_done"},
                {"witnesses": {"sam": "none", "by_person": True}, "ask_for": "closer"},
                {"witnesses": {"sam": "none", "by_person": True}, "clause_ids": ["c1"]}):
        with pytest.raises(ValidationError):
            v.validate({**base, **bad})


def test_a_procedure_the_build_could_not_make_into_steps_keeps_its_heading() -> None:
    card = json.loads((EXAMPLES / "card" / "acme-wm-0000.json").read_text())
    ok = [{"reason": "not_built", "heading": "Den Wasserzulauf entkalken", "page": 31},
          {"id": "replace_heater", "title": {"en": "Replace the heater"}, "reason": "mains", "page": 5}]
    contracts.validate("card", {**card, "refused": ok})
    for bad in ({"reason": "not_built", "page": 31},                                    # no heading
                {"reason": "not_built", "heading": "x", "page": 31, "id": "descale"},   # not a job
                {"reason": "not_built", "heading": "x" * 121, "page": 31},
                {"id": "replace_heater", "title": {"en": "t"}, "reason": "mains", "page": 5, "heading": "x"}):
        with pytest.raises(ValidationError):
            contracts.validate("card", {**card, "refused": [bad]})


def test_store_migration_9_the_door_opens_only_by_an_operator_row() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(contracts.STORE_SQL.read_text())
    assert db.execute("pragma user_version").fetchone()[0] >= 9
    assert db.execute("select state from door_log order by id desc limit 1").fetchone() is None   # closed
    db.execute("insert into door_log (state, by, at) values ('open', 'Operator', '2026-10-27T18:00:00Z')")
    db.execute("insert into door_log (state, by, at) values ('closed', 'Operator', '2026-10-27T19:00:00Z')")
    assert db.execute("select state from door_log order by id desc limit 1").fetchone() == ("closed",)
    for bad in (("ajar", "Operator"), ("open", ""), ("open", "x" * 61), ("open", None)):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into door_log (state, by, at) values (?, ?, 't')", bad)
    assert "door_log" in _migrations()[9]
    cols = {r[1]: r for r in db.execute("pragma table_info(return_codes)")}
    assert "person_id" in cols
    fks = {r[3]: r[2] for r in db.execute("pragma foreign_key_list(return_codes)")}
    assert fks["person_id"] == "people"


def test_the_door_says_whether_it_would_open_a_home() -> None:
    v = _openapi_validator("DoorState")
    midnight = "2026-10-28T00:00:00Z"
    for ok in ({"state": "open", "reopens_at": None}, {"state": "shut", "reopens_at": None},
               {"state": "capped", "reopens_at": None}, {"state": "capped", "reopens_at": midnight}):
        assert v.is_valid(ok), list(v.iter_errors(ok))
    for bad in ({}, {"state": "open"}, {"state": "open", "reopens_at": midnight},
                {"state": "shut", "reopens_at": midnight}, {"state": "ajar", "reopens_at": None}, {"open": True},
                {"state": "open", "reopens_at": None, "why": "x"}):
        assert not v.is_valid(bad), bad
    door = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]["/households/door"]
    assert set(door) == {"get"} and door["get"]["security"] == []


def test_a_deleted_household_closes_its_sessions_for_good() -> None:
    gone = contracts.validator("ws", "GoneClose")
    assert gone.is_valid({"error": "household_deleted"})
    for bad in ({}, {"error": "home_cap"}, {"error": "household_deleted", "reopens_at": None}):
        assert not gone.is_valid(bad), bad
    defs = json.loads(contracts.schema_path("ws").read_text())["$defs"]
    codes = [d["x-close-code"] for d in defs.values() if "x-close-code" in d]
    assert sorted(codes) == [4410, 4429]                                     # one code, one meaning


def test_store_migration_10_a_push_is_logged_per_person() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(contracts.STORE_SQL.read_text())
    db.execute("pragma foreign_keys = on")
    assert "person_pushes" in _migrations()[10]
    fks = {r[3]: r[2] for r in db.execute("pragma foreign_key_list(person_pushes)")}
    assert fks == {"person_id": "people"}
    pk = [r[1] for r in sorted(db.execute("pragma table_info(person_pushes)"), key=lambda r: r[5]) if r[5]]
    assert pk == ["person_id", "reminder_key", "at"]
    with pytest.raises(sqlite3.IntegrityError):                                  # no such person
        db.execute("insert into person_pushes values ('nobody', 'k', '2026-10-27T18:00:00Z')")


def test_only_an_admin_deletes_the_home() -> None:
    household = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]["/household"]
    for verb in ("patch", "delete"):
        assert "403" in household[verb]["responses"] and "admins only" in household[verb]["summary"], verb


def test_a_token_is_made_for_an_agent_or_home_assistant_only() -> None:
    post = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]["/household/tokens"]["post"]
    body = post["requestBody"]["content"]["application/json"]["schema"]
    assert body["properties"]["by_kind"]["enum"] == ["agent", "ha"]          # a calendar address is a feed URL
    assert "/household/feeds" in post["responses"]["422"]["description"]


def test_a_feedback_answer_is_kept_as_typed_and_quoted_only_by_consent() -> None:
    rec = json.loads((EXAMPLES / "feedback" / "app-answer.json").read_text())
    contracts.validate("feedback", rec)
    typed = {**rec["answers"], "hard": "  de klep\n"}                          # never trimmed
    for ok in ({"answers": typed}, {"quote": "named", "quote_name": "Sam"}, {"quote": "none"},
               {"withdrawn_at": "2026-10-29T08:00:00Z"}, {"source": "pdf", "lang": None}):
        contracts.validate("feedback", {**rec, **ok})
    withdrawn = {"quote": "named", "quote_name": None, "withdrawn_at": "2026-10-29T08:00:00Z"}   # the name is blanked
    contracts.validate("feedback", {**rec, **withdrawn})
    for bad in ({"quote": "anonymous", "quote_name": "Sam"}, {"quote": "named", "quote_name": ""},
                {"quote": "named", "quote_name": None}, {"answers": {**rec["answers"], "hard": "x" * 2001}},
                {"id": "fb-xyz"}, {"form_version": 2}, {"source": "email"},
                {"answers": {**rec["answers"], "would_use": "sometimes"}},
                {"answers": {k: v for k, v in rec["answers"].items() if k != "friend"}},
                {"answers": {**rec["answers"], "rating": 5}}):
        with pytest.raises(ValidationError):
            contracts.validate("feedback", {**rec, **bad})


def test_store_migration_11_one_pilot_cohort_rule() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(contracts.STORE_SQL.read_text())
    db.execute("insert into invite_links values ('l1', 'x', 'judges', 'Judges', 0.5, 20, 40, 't', NULL, 't')")
    homes = {"in": "", "no_consent": "", "deleted": "", "own": "", "test": "", "door": "", "link": ""}
    for h in homes:
        db.execute("insert into households (id, invite_code, created_at, consent_at) values (?, ?, 't', 't')", (h, h))
    db.execute("update households set consent_at = NULL where id = 'no_consent'")
    db.execute("update households set deleted_at = 't' where id = 'deleted'")
    db.execute("update households set is_own = 1 where id = 'own'")
    db.execute("update households set is_test = 1 where id = 'test'")
    db.execute("update households set joined_via = 'door' where id = 'door'")
    db.execute("update households set joined_via = 'link', invite_link_id = 'l1' where id = 'link'")
    assert [r[0] for r in db.execute("select id from pilot_cohort")] == ["in"]


def test_feedback_in_is_the_record_without_what_the_server_sets() -> None:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    comp = yaml.safe_load(contracts.OPENAPI.read_text())["components"]["schemas"]
    root = json.loads(json.dumps({"$id": "https://fixit.invalid/contracts/api.openapi.yaml", "$defs": comp,
                                  **comp["FeedbackIn"]}).replace("#/components/schemas/", "#/$defs/"))
    fb = json.loads(contracts.schema_path("feedback").read_text())
    v = Draft202012Validator(root, registry=Registry().with_resource(fb["$id"], Resource.from_contents(fb)))
    rec = json.loads((contracts.HERE / "examples" / "feedback" / "app-answer.json").read_text())
    body = {k: rec[k] for k in ("lang", "answers")} | {"quote": "named", "quote_name": "Operator"}
    assert v.is_valid(body)
    anon = {**body, "quote": "anonymous", "quote_name": None}
    assert v.is_valid(anon) and v.is_valid({k: x for k, x in anon.items() if k != "quote_name"})
    for bad in ({**body, "quote_name": None}, {**anon, "quote_name": "Operator"}, {**body, "id": rec["id"]},
                {**body, "household": "h"}, {**body, "answers": {**body["answers"], "extra": None}},
                {**body, "lang": "de"}, {**body, "quote_name": ""}):
        assert not v.is_valid(bad), bad
    assert not v.is_valid({k: x for k, x in body.items() if k != "lang"})
    assert not v.is_valid({**body, "answers": {k: x for k, x in body["answers"].items() if k != "friend"}})
    paths = yaml.safe_load(contracts.OPENAPI.read_text())["paths"]
    post = paths["/household/feedback"]["post"]
    assert set(post["responses"]) == {"201", "403", "410", "422", "429", "503"}
    assert "get" not in paths["/household/feedback"]  # no route reads feedback back
    ok = post["responses"]["201"]["content"]["application/json"]["schema"]
    assert Draft202012Validator(ok).is_valid({"id": rec["id"]}) and not Draft202012Validator(ok).is_valid({"id": "x"})
    assert set(paths["/household/feedback/{feedback_id}/withdraw"]["post"]["responses"]) == {"204", "403", "404", "503"}
    hh = _openapi_validator("Household")
    assert not hh.is_valid({"id": "h", "country": "nl", "lang": "nl"})  # pilot is always said


def test_due_name_is_optional_and_never_empty() -> None:
    v = _openapi_validator("Due")
    due = yaml.safe_load(contracts.OPENAPI.read_text())["components"]["schemas"]["Due"]
    assert "due_name" not in due["required"]
    ok = {"appliance_id": "a", "job_id": "machine_clean", "title": "Run the machine-clean programme", "due_on": None,
          "last_done": {"date": None, "source": "unknown"}, "interval_quote": "from your manual, p. 12",
          "page": 12, "reason": "unanswered", "quote": "Elke maand.",
          "manual": {"every": 1, "unit": "month", "page": 12, "quote": "Elke maand."}, "schedule": None,
          "window_end": None, "status": "none", "snoozed_until": None, "assignee": None}  # as before 1.40
    assert v.is_valid(ok), list(v.iter_errors(ok))
    assert v.is_valid({**ok, "due_name": "Machine clean"})
    assert not v.is_valid({**ok, "due_name": ""}) and not v.is_valid({**ok, "due_name": None})


def test_job_needs_cite_the_card_and_readiness_is_the_persons_word() -> None:
    import copy

    card = json.loads((EXAMPLES / "card" / "acme-wm-0000.json").read_text())
    for none in (None, []):
        contracts.load_card({**card, "jobs": [{**card["jobs"][0], "needs": none}]})
    towel = {"id": "towel", "what": {"en": "A towel"}, "kind": "tool", "quote_ref": "q_arrows"}
    cleaner = {"id": "cleaner", "what": {"en": "Machine cleaner"}, "quote_ref": "q_interval"}  # kind is optional
    card["jobs"][0]["needs"] = [towel, cleaner]
    contracts.validate("card", card)
    contracts.load_card(card)
    for bad in ({"kind": "chemical"}, {"what": None}, {"quote_ref": None}, {"page": 4}, {"what": {}}):
        two = copy.deepcopy(card)
        two["jobs"][0]["needs"] = [{k: v for k, v in {**towel, **bad}.items() if v is not None}]
        with pytest.raises(ValidationError):
            contracts.validate("card", two)
    broken = copy.deepcopy(card)
    broken["jobs"][0]["needs"] = [towel, {**towel, "quote_ref": "q_nope"}]
    assert contracts.card_invariants(broken) == [
        "job 'clean_debris_filter': need id 'towel' is used 2 times",
        "job 'clean_debris_filter': need 'towel': quote_ref 'q_nope' is not in quotes",
    ]
    with pytest.raises(contracts.ContractError):
        contracts.load_card(broken)
    # the preview carries the manual's words for every need and step; the answer only names needs, and is the person's
    preview = json.loads((EXAMPLES / "ws" / "JobPreview--cleaner.json").read_text())
    assert unions.s2c.validate_python(preview).type == "job_preview"
    assert all(i["page"] and i["quote"] for i in preview["needs"])
    assert all(st.get("origin") == "fixit" or (st["page"] and st["quote"]) for st in preview["steps"])
    contracts.validate("ws", {**preview, "needs": []})
    ready = unions.c2s.validate_python({"type": "job_ready", "seq": 0, "t_ms": 0, "job_id": "j", "ready": []})
    assert ready.ready == []
    with pytest.raises(ValidationError):
        contracts.validate("ws", {"type": "job_ready", "seq": 0, "t_ms": 0, "job_id": "j", "ready": ["a", "a"]})
    with pytest.raises(ValidationError):
        contracts.validate("ws", {**preview, "steps": []})


def test_a_step_without_the_camera_has_no_zone_red_part_or_power() -> None:
    import copy

    card = json.loads((EXAMPLES / "card" / "acme-wm-0000.json").read_text())
    assert all("camera" not in st for st in card["jobs"][0]["steps"])  # absent means check: today's cards unchanged
    unplug, open_flap, refit = card["jobs"][0]["steps"]
    assert contracts.card_invariants(card) == []
    ok = copy.deepcopy(card)
    ok["jobs"][0]["steps"][1]["camera"] = "find"                        # a power-off step may find; the plug gate stays
    ok["jobs"][0]["steps"][1]["power"] = "any"
    ok["jobs"][0]["steps"][0] = {**unplug, "power": "any", "camera": "none"}
    contracts.load_card(ok)
    for bad in ({"camera": "photo"}, {"camera": None}):
        two = copy.deepcopy(card)
        two["jobs"][0]["steps"][0] = {**unplug, **bad}
        with pytest.raises(ValidationError):
            contracts.validate("card", two)
    broken = copy.deepcopy(card)
    broken["jobs"][0]["steps"] = [
        {**unplug, "camera": "none"},                                                  # power off
        {**open_flap, "power": "any", "camera": "none", "parts": ["back_panel"]},      # a red-zone part
        {**refit, "power": "any", "camera": "none", "zone_rings": [{"zone_id": "casing", "ring_cm": 4}]},
        {**refit, "id": "find_nothing", "camera": "find", "parts": []},
    ]
    assert contracts.card_invariants(broken) == [
        "step 'unplug': camera none, but it has power off",
        "step 'open_flap': camera none, but it has a red-zone part",
        "step 'refit_filter': camera none, but it has zones",
        "step 'refit_filter': camera none, but it has zone rings",
        "step 'find_nothing': camera find, but it names no part to find",
    ]
    with pytest.raises(contracts.ContractError):
        contracts.load_card(broken)
    step = json.loads((EXAMPLES / "ws" / "StepMsg--refit.json").read_text())
    for cam in ("check", "find", "none"):
        contracts.validate("ws", {**step, "camera": cam})


def test_sentence_and_held_may_name_their_step() -> None:
    sentence = json.loads((EXAMPLES / "ws" / "Sentence--ok.json").read_text())
    held = json.loads((EXAMPLES / "ws" / "Held--casing.json").read_text())
    for msg in (sentence, held):
        contracts.validate("ws", msg)                                   # absent: not tied to a step
        tagged = unions.s2c.validate_python({**msg, "step_id": "open_flap"})
        assert tagged.step_id == "open_flap"
        with pytest.raises(ValidationError):
            contracts.validate("ws", {**msg, "step_id": ""})


def test_a_tap_may_name_the_step_the_person_sees() -> None:
    tap = {"type": "tap", "seq": 0, "t_ms": 0, "action": "did_it"}
    contracts.validate("ws", tap)                                       # absent: the current step
    assert unions.c2s.validate_python({**tap, "step_id": "open_flap"}).step_id == "open_flap"
    with pytest.raises(ValidationError):
        contracts.validate("ws", {**tap, "step_id": ""})
    contracts.validate("ws", {"type": "notice", "seq": 0, "t_ms": 0, "kind": "tap_not_current"})  # the refusal's why
