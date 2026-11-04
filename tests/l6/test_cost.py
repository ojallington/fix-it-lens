import json

import pytest

from eval.cost import _spread, claims, collect

LEDGER = [
    {"role": "guide", "session_id": "sess-a", "status": "completed", "usd": 0.01, "input_tokens": 1000},
    {"role": "guide", "session_id": "sess-a", "status": "completed", "usd": 0.03},
    {"role": "guide", "session_id": "sess-a", "status": "timeout", "usd": 0.06},  # an estimate: left out
    {"role": "guide", "session_id": None, "status": "completed", "usd": 0.0003},  # not a turn in a session
    {"role": "sam", "session_id": "sess-a", "status": "completed", "usd": 0.0025, "images_processed": 1},
    {"role": "sam", "mode": "local", "status": "completed", "usd": 0.0},
    {"role": "judge", "provider": "fireworks", "status": "completed", "usd": 0.5},  # a partner: not a Meta price
    {"role": "guide", "session_id": "pilot-x", "household": "h_1", "status": "completed", "usd": 9.0},
    {"probe": "appliance_file", "run": "card-build:bosch/X1:aa", "status": "completed", "usd": 0.6},
    {"probe": "appliance_file", "run": "card-build:bosch/X1:aa", "status": "completed", "usd": 0.1},
    {"role": "plate", "run": "add-appliance:unknown/unknown:aa", "status": "completed", "usd": 0.01},  # join: the id
    {"role": "plate", "run": "add-appliance:bosch/X1:bb", "status": "completed", "usd": 0.02},
    {"lane": "probe", "usd": 0.38},
]


def test_collect_drops_households_and_session_ids() -> None:
    rows = collect(LEDGER, ["sess-a"])
    text = json.dumps(rows)
    assert "pilot" not in text and "h_1" not in text and "sess-a" not in text and len(rows) == 11
    assert {r["session"] for r in rows} == {"s01", None}


def test_per_call_claims_are_medians_with_their_spread() -> None:
    c = {x["id"]: x for x in claims("calls", collect(LEDGER, ["sess-a"]))}
    g = c["cost.call.guide.usd_median"]
    assert g["value"] == 0.02 and g["n"] == 2 and "1 timed-out" in g["note"]
    assert "cost.call.guide.usd_p10" not in c  # two calls: the median alone
    assert c["cost.local.api_usd"] == {**c["cost.local.api_usd"], "value": 0.0, "n": 1}
    assert c["cost.call.judge.usd_median"]["n"] == 0  # no judge calls: listed as not measured yet


def test_five_values_or_more_carry_their_percentiles() -> None:
    c = {x["id"]: x["value"] for x in _spread("x", [0.5, 0.1, 0.2, 0.4, 0.3], "$", "")}
    assert c == {"x_median": 0.3, "x_p10": 0.1, "x_p90": 0.5}


def test_card_and_add_runs_group_by_their_tag() -> None:
    from eval.cost import part_card

    c = {x["id"]: x for x in part_card(collect(LEDGER, []))}
    assert c["cost.card_build.usd_median"]["value"] == 0.7
    assert c["cost.add_appliance.new.usd_median"]["value"] == 0.71
    assert c["cost.add_appliance.known.usd_median"]["value"] == 0.02


def test_session_totals_and_parts_without_data() -> None:
    c = {x["id"]: x for x in claims("session", collect(LEDGER, ["sess-a"]))}
    assert c["cost.repair_session.usd_median"]["value"] == 0.0425
    assert [x["n"] for x in claims("session", collect(LEDGER, []))] == [0]
    assert "projected" in claims("home", [])[0]["unit"]


def test_a_judges_home_never_reaches_a_cost_number() -> None:
    # a judge's home (households.invite_link_id set) is a household like any other in the ledger: every line that
    # names a household is dropped, whatever the session, run tag or provider
    judge = {"household": "h_judge01", "status": "completed", "usd": 5.0}
    led = [{**judge, "role": "guide", "session_id": "sess-a"}, {**judge, "role": "judge"},
           {**judge, "probe": "appliance_file", "run": "card-build:bosch/X1:cc"},
           {**judge, "role": "plate", "run": "add-appliance:bosch/X1:cc"}]
    rows = collect(LEDGER + led, ["sess-a"])
    assert "h_judge01" not in json.dumps(rows) and len(rows) == len(collect(LEDGER, ["sess-a"]))
    for part in ("calls", "session"):
        assert claims(part, rows) == claims(part, collect(LEDGER, ["sess-a"]))
    from eval.cost import part_card

    assert part_card(rows) == part_card(collect(LEDGER, ["sess-a"]))


def test_a_build_outside_the_roster_is_not_a_price() -> None:
    from eval.cost import part_card

    led = [{"probe": "appliance_file", "run": "card-build:acme/WM-0000:dd", "status": "completed", "usd": 0.29}]
    [c] = [x for x in part_card(collect(LEDGER + led, []), {"bosch/X1"}) if x["id"].startswith("cost.card_build")]
    assert (c["value"], c["n"]) == (0.7, 1) and "roster" in c["note"]


def test_each_any_appliance_run_is_its_own_price_by_its_build_tags() -> None:
    from eval.cost import part_card

    led = [{"probe": "appliance_file", "run": f"card-build:bosch/X1:{t}", "status": "completed", "usd": u}
           for t, u in (("b1", 1.0), ("r1", 2.0), ("r1", 0.5), ("x9", 9.0))]
    c = {x["id"]: x for x in part_card(collect(led, []), {"bosch/X1"}, {"baseline": {"b1"}, "rerun": {"r1"}})}
    assert (c["cost.card_build.usd_median"]["value"], c["cost.card_build.usd_median"]["n"]) == (1.0, 1)
    assert (c["cost.card_build.rerun.usd_median"]["value"], c["cost.card_build.rerun.usd_median"]["n"]) == (2.5, 1)
    with pytest.raises(ValueError, match="collect"):
        part_card(collect(led, []), {"bosch/X1"}, {"rerun": {"zz"}})


def test_a_filmed_repair_in_our_own_home_is_kept_without_the_home() -> None:
    # the golden sessions run in our own household (is_own): listed, their calls count; the household never shows
    own = {"role": "guide", "session_id": "sess-g", "household": "h_own1", "status": "completed", "usd": 0.2}
    led = LEDGER + [own, {**own, "role": "judge", "usd": 0.05}]
    assert "h_own1" not in json.dumps(collect(led, ["sess-a", "sess-g"], {"h_own1"}))
    c = {x["id"]: x for x in claims("session", collect(led, ["sess-a", "sess-g"], {"h_own1"}))}
    assert c["cost.repair_session.usd_median"]["n"] == 2
    assert len(collect(led, ["sess-a"], {"h_own1"})) == len(collect(LEDGER, ["sess-a"]))  # not listed: dropped
    assert len(collect(led, ["sess-a", "sess-g"])) == len(collect(LEDGER, ["sess-a"]))  # not our own: dropped
