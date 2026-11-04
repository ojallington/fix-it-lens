"""Ingest → card on the fake Acme manual, from synthetic fixtures: the card validates, every quote in it passes the
citation check, and a planted bad quote is dropped from the card and listed."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import calls
import contracts
from calls.testing import responses_events, write_respond_fixture
from ingest import appliance_file as af
from ingest import jobcheck
from ingest.citation import check_quote, verify
from ingest.pages import extract, page_hash

WONDER = "muse-wonder-1.0"

DRAFT: dict[str, Any] = {
    "appliance_kind": "washer", "manual_lang": "en", "title": "Acme WM-0000 user manual", "doc_id": "AC-0000-EN",
    "parts": [
        {"id": "debris_filter", "name_manual": "debris filter", "name_en": "debris filter", "locate": "appearance",
         "cue": "round knob behind a small flap, bottom right", "sam_text": "filter knob", "kind": "allowed"},
        {"id": "filter_flap", "name_manual": "filter flap", "name_en": "filter flap", "locate": "appearance",
         "cue": "small flap, bottom right", "sam_text": "small flap", "kind": "allowed"},
        {"id": "back_panel", "name_manual": "back panel", "name_en": "back panel", "locate": "function",
         "cue": "metal panel at the back", "sam_text": "back panel", "kind": "red_zone"},
        {"id": "drum", "name_manual": "drum", "name_en": "drum", "locate": "appearance",
         "cue": "the steel drum", "sam_text": "drum", "kind": "allowed"},
    ],
    "red_zones": [
        {"id": "casing", "part_id": "back_panel", "rule": "mains.casing", "page": 2,
         "quote": "Do not open the casing. Behind the back panel is mains power."},
    ],
    "jobs": [
        {"id": "clean_debris_filter", "title_en": "Clean the debris filter", "title_manual": "Clean the debris filter",
         "kind": "upkeep", "interval": {"every": 3, "unit": "month", "page": 3,
                                        "quote": "Clean the debris filter every 3 months."},
         "fault_codes": ["E21"], "landlord": False,
         "duration": {"minutes": 10, "page": 4, "quote": "The whole job takes about 10 minutes."},
         "steps": [
             # cited on page 3; it is on page 4, so code moves it
             {"id": "open_flap", "text_en": "Open the filter flap.", "text_manual": "Open the filter flap.", "page": 3,
              "quote": "Open the filter flap at the bottom right of the front panel.",
              "done_when": [{"id": "flap_open", "text_en": "The flap is open"}], "parts": ["filter_flap"],
              "power": "any", "zones": [], "view": "front", "look_only_lines": []},
             {"id": "wait_drum_empty", "text_en": "Do not take the filter out while there is water in the drum.",
              "text_manual": "Do not remove the filter while the drum still holds water.", "page": 4,
              "quote": "Do not remove the filter while the drum still holds water.",
              "done_when": [{"id": "drum_empty", "text_en": "The drum holds no water"}], "parts": ["debris_filter"],
              "power": "any", "zones": [], "view": "front", "look_only_lines": []},
             {"id": "drain", "text_en": "Put a tray under the filter and turn the knob anticlockwise.",
              "text_manual": "Put a tray under the filter and turn the knob anticlockwise.", "page": 4,
              "quote": "Place a shallow tray under the filter and turn the filter knob anticlockwise.",
              "done_when": [{"id": "tray", "text_en": "The tray is under the filter"}], "parts": ["debris_filter"],
              "power": "any", "zones": [], "view": "front", "look_only_lines": []},
             {"id": "refit", "text_en": "Screw the filter back in.", "text_manual": "Screw the filter back in.",
              "page": 4, "quote": "Screw the filter back in clockwise until the arrow marks line up.",
              "done_when": [{"id": "arrows", "text_en": "The arrow marks line up"}], "parts": ["debris_filter"],
              "power": "off", "zones": ["casing"], "view": "side",
              "look_only_lines": ["The back panel stays on; you only look at the front."]},
         ]},
        # the planted bad quote: a dropped negation
        {"id": "clean_drum", "title_en": "Clean the drum", "title_manual": "Clean the drum", "kind": "upkeep",
         "interval": None, "fault_codes": [], "landlord": False, "duration": None,
         "steps": [
             {"id": "solvent", "text_en": "Clean the drum.", "text_manual": "Clean the drum.", "page": 3,
              "quote": "Use solvents to clean the drum.", "done_when": [], "parts": ["drum"], "power": "off",
              "zones": [], "view": "front", "look_only_lines": []},
         ]},
    ],
    "keywords": ["debris filter", "E21"],
    "upkeep_procedures": [{"heading": "Cleaning the debris filter", "page": 4, "job_id": "clean_debris_filter",
                           "drop_reason": ""}],
}

REPAIR_MISS = {"fixes": [{"key": "jobs[clean_drum].steps[solvent]", "page": 3, "quote": None}]}


def _client(tmp_path: Path, acme_pdf: Path, draft: dict[str, Any], repair: dict[str, Any] | None) -> calls.Client:
    client = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    body = client.build_respond_body("appliance_file", af.request_input(acme_pdf.read_bytes(), acme_pdf.name),
                                     instructions=af.INSTRUCTIONS, format=af.FILE_FORMAT)
    write_respond_fixture(tmp_path, body, responses_events(text=json.dumps(draft), model=WONDER))
    if repair is not None:
        texts = [p.text for p in extract(acme_pdf)]
        d = copy.deepcopy(draft)
        af._normalise_ids(d)
        checker = af._Checker(extract(acme_pdf))
        failed = [(w, o) for w, o in af._cites(d) if not checker.check(w, int(o["page"]), o["quote"]).ok]
        rbody = client.build_respond_body("appliance_file", af.repair_input(failed, texts),
                                          instructions=af.REPAIR_INSTRUCTIONS, format=af.REPAIR_FORMAT, effort="low")
        write_respond_fixture(tmp_path, rbody, responses_events(text=json.dumps(repair), model=WONDER))
    return client


async def _build(tmp_path: Path, acme_pdf: Path, draft: dict[str, Any],
                 repair: dict[str, Any] | None = REPAIR_MISS) -> af.IngestResult:
    client = _client(tmp_path, acme_pdf, draft, repair)
    return await af.build(acme_pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme",
                          country="xx", client=client)


async def test_card_validates_and_every_quote_passes(tmp_path: Path, acme_pdf: Path) -> None:
    res = await _build(tmp_path, acme_pdf, DRAFT)
    assert res.card is not None, res.error
    jsonschema.validate(res.card, af.CARD_SCHEMA)
    pages = [p.text for p in extract(acme_pdf)]
    card = res.card
    quotes = [z["quote"] for z in card["red_zones"]] + card["quotes"]
    quotes += [s["quote"] for j in card["jobs"] for s in j["steps"]]
    assert len(quotes) == 11  # 1 red zone, interval, duration, 4 steps, and the 4 steps again as done_when citations
    for q in quotes:
        assert check_quote(pages[q["page"] - 1], q["text"]).ok, q
    assert card["manual"]["page_text_sha256"] == [page_hash(t) for t in pages]
    assert card["red_zones"][0]["rule_id"] == "xx.mains.casing"
    assert card["appliance_file_model"] == WONDER


async def test_planted_bad_quote_is_dropped_and_listed(tmp_path: Path, acme_pdf: Path) -> None:
    res = await _build(tmp_path, acme_pdf, DRAFT)
    assert res.card is not None
    assert [j["id"] for j in res.card["jobs"]] == ["clean_debris_filter"]
    assert "drum" not in {p["id"] for p in res.card["parts"]}
    assert len(res.rejected) == 1
    r = res.rejected[0]
    assert r.where == "jobs[clean_drum].steps[solvent]" and r.reason == "critical_tokens"
    assert "clean_drum" in r.dropped
    assert res.rail == "muse-wonder: manual → 4 steps · 1 red zones · 1 interval · 1 rejected, the rest cited"


async def test_wrong_page_is_corrected(tmp_path: Path, acme_pdf: Path) -> None:
    res = await _build(tmp_path, acme_pdf, DRAFT)
    assert res.card is not None
    step = next(st for st in res.card["jobs"][0]["steps"] if st["id"] == "open_flap")
    assert step["page"] == 4 and step["quote"]["page"] == 4
    assert ("jobs[clean_debris_filter].steps[open_flap]", 3, 4) in res.corrected


async def test_repair_round_keeps_a_fixed_quote(tmp_path: Path, acme_pdf: Path) -> None:
    fixed = {"fixes": [{"key": "jobs[clean_drum].steps[solvent]", "page": 3,
                        "quote": "Do not use solvents to clean the drum."}]}
    res = await _build(tmp_path, acme_pdf, DRAFT, fixed)
    assert res.card is not None and not res.rejected
    assert res.rail.endswith("every one cited")
    assert res.card["jobs"][1]["steps"][0]["quote"]["text"] == "Do not use solvents to clean the drum."


async def test_red_zone_with_a_bad_quote_is_listed(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["red_zones"][0]["quote"] = "Open the casing. Behind the back panel is mains power."
    d["jobs"] = d["jobs"][:1]
    repair = {"fixes": [{"key": "red_zones[casing]", "page": 2, "quote": None}]}
    res = await _build(tmp_path, acme_pdf, d, repair)
    assert res.card is not None and res.card["red_zones"] == []
    assert [r.where for r in res.rejected] == ["red_zones[casing]"]
    assert all(s["zones"] == [] for s in res.card["jobs"][0]["steps"])


async def test_failed_ingest_keeps_plain_pages(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][1:]  # only the bad job: nothing survives
    res = await _build(tmp_path, acme_pdf, d)
    assert res.card is None and res.error
    assert res.rail == "muse-wonder: manual → no card · kept as plain pages"


async def test_missing_fixture_is_a_failed_ingest(tmp_path: Path, acme_pdf: Path) -> None:
    client = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    res = await af.build(acme_pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/a",
                         client=client)
    assert res.card is None and "MissingFixture" in (res.error or "")


@pytest.mark.parametrize(("text", "n"), [("One. Two.", 2), ("E99: do not. Call a technician.", 2),
                                         ("One. Two. Three.", 3), ("Rated 230 V, 50 Hz.", 1)])
def test_sentence_count(text: str, n: int) -> None:
    assert af.sentences(text) == n


async def test_recorded_wonder_card_on_acme(tmp_path: Path, acme_pdf: Path) -> None:
    """The Wonder probe (re-recorded 27 Oct for whole procedures, the checklist and the clause retry's page, $0.58):
    the real model's card on the fake manual, every quote cited. Its filter job now carries the manual's own line
    between two of its steps ("Do not remove the filter while the drum still holds water."), and the manual never
    plugs the machine back in, so our restore step finishes it. Its E21 fault job keeps a clause about the drum on
    a step that never names it, after the one retry: the gate drops that job and the card lists it."""
    pdf = tmp_path / "WM-0000.pdf"
    pdf.write_bytes(acme_pdf.read_bytes())
    client = calls.make_client("replay", fixtures_root=calls.FIXTURES, private=None)
    res = await af.build(pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme/wm-0000",
                         country="xx", client=client)
    assert res.card is not None, res.error
    assert res.card["appliance_file_model"] == WONDER
    assert [(r.where.split(".")[0], r.reason.split(":")[0]) for r in res.rejected] == [
        ("jobs[clean_the_debris_filter_fault_e21]", "clause_off_step"),
        ("troubleshooting[fault_e99]", "troubleshooting")]
    job = next(j for j in res.card["jobs"] if j["id"] == "clean_the_debris_filter")
    assert "Do not remove the filter while the drum still holds water." in [
        (s.get("quote") or {}).get("text") for s in job["steps"]]
    assert [s["origin"] for s in job["steps"] if s.get("origin") == "fixit"] == ["fixit"]
    assert job["steps"][-1]["id"] == "clean_the_debris_filter_restore_mains"
    assert ("clean_the_debris_filter", "job") in {(c["job_id"], c["status"]) for c in res.coverage}
    assert contracts.card_invariants(res.card) == []
    assert res.card["manual"]["models"] == ["WM-0000"] and res.card["manual"]["models_page"] == 1
    assert {c for r in res.card["troubleshooting"] for c in r["codes"]} == {"E21"}  # E99's row has no remedy
    assert res.rail.startswith("muse-wonder: manual → ")
    pages = [p.text for p in extract(pdf)]
    for j in res.card["jobs"]:
        for s in j["steps"]:
            if s.get("origin") != "fixit":  # our own step quotes nothing
                assert check_quote(pages[s["quote"]["page"] - 1], s["quote"]["text"]).ok


async def test_witness_kept_only_for_a_known_part(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    steps = d["jobs"][0]["steps"]
    steps[0]["witness"] = None
    steps[1]["witness"] = {"part": "filter_flap", "expect": "present", "anchor": "nowhere"}
    steps[3]["witness"] = {"part": "Debris Filter", "expect": "present", "anchor": None}
    d["jobs"] = d["jobs"][:1]
    res = await _build(tmp_path, acme_pdf, d, None)
    assert res.card is not None, res.error
    got = [s.get("witness") for s in res.card["jobs"][0]["steps"]]
    assert got == [None, {"part": "filter_flap", "expect": "present", "anchor": None}, None,
                   {"part": "debris_filter", "expect": "present", "anchor": None}]


async def test_boiler_jobs_off_the_allow_list_are_dropped(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["appliance_kind"] = "boiler"
    d["red_zones"][0]["rule"] = "gas.tap"
    d["jobs"] = d["jobs"][:1] + copy.deepcopy(d["jobs"][:1])
    d["jobs"][0]["task"] = "other"
    d["jobs"][1]["id"], d["jobs"][1]["task"] = "fill_and_bleed_installation", "top_up"  # the id the model chose
    res = await _build(tmp_path, acme_pdf, d, None)
    assert res.card is not None, res.error
    assert [j["id"] for j in res.card["jobs"]] == ["top_up"]  # the task names it, not the model's id
    assert [(r.where, r.reason) for r in res.rejected] == [
        ("jobs[clean_debris_filter]", "not_on_boiler_allow_list: task other")]
    assert "(" in res.rejected[0].dropped  # listed with its title, never silently
    # and on the card, for the "how this card was checked" panel: a name and a page, never its steps
    assert res.card["refused"] == [{"id": "clean_debris_filter", "title": {"en": "Clean the debris filter"},
                                    "reason": "gas", "page": 4}]
    refit = res.card["jobs"][0]["steps"][3]
    assert refit["look_only_lines"] == af.GAS_TAP_LOOK_ONLY  # byte-exact, whatever the model wrote
    assert res.card["red_zones"][0]["rule_id"] == "xx.gas.tap"
    # off the list and away from gas and mains: refused as other, not as a professional's job the manual never named
    plain = {**d["jobs"][0], "steps": [{**st, "zones": []} for st in d["jobs"][0]["steps"]]}
    assert af.refusal_reason(plain, d["red_zones"]) == "other"


def test_a_boiler_job_with_task_other_is_dropped_whatever_its_id() -> None:
    d = {"appliance_kind": "boiler", "jobs": [{"id": "top_up_x", "task": "other", "after": []},
                                              {"id": "a", "task": "top_up", "after": []},
                                              {"id": "b", "task": "top_up", "after": [{"job_id": "a"}]}]}
    af.boiler_ids(d)
    assert [j["id"] for j in d["jobs"]] == ["top_up_x", "top_up", "top_up_2"]
    assert d["jobs"][2]["after"][0]["job_id"] == "top_up"


def test_a_checklist_answer_follows_its_boiler_job_to_the_new_id() -> None:
    # the reader names the job by the draft's own id; a built job renamed to its task is still the job it named
    d = {"appliance_kind": "boiler", "jobs": [{"id": "fill_and_bleed", "task": "top_up", "after": []}],
         "checklist": [{"item": "boiler.top_up_pressure", "job_id": "fill_and_bleed"},
                       {"item": "boiler.drain_system", "job_id": None}]}
    af.boiler_ids(d)
    assert [c["job_id"] for c in d["checklist"]] == ["top_up", None]


def test_the_task_list_is_the_rules_sealed_boiler_allow_list() -> None:
    import yaml

    rules = yaml.safe_load((Path(__file__).resolve().parents[2] / "rules" / "nl.yaml").read_text())
    assert [x["id"] for x in rules["allow_lists"]["sealed_boiler"]] == af.BOILER_JOBS
    assert [*af.BOILER_JOBS, "other"] == af.TASKS


@pytest.mark.parametrize(("rule", "out"), [("mains.casing", "mains.casing"), ("mains.back_panel", "mains.casing"),
                                           ("mains.mains_cable", None), ("gas.burner", "gas.tap"),
                                           ("co.flue", "gas.flue"), ("mechanical.pump_impeller", None)])
def test_zone_remap(rule: str, out: str | None) -> None:
    assert af.zone_rule(rule) == out


def test_look_only_lines_are_l4s_bytes() -> None:
    assert "'" in af.GAS_TAP_LOOK_ONLY[0] and "’" not in af.GAS_TAP_LOOK_ONLY[0]


async def test_due_name_after_and_red_zone_keywords(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][:1] + [copy.deepcopy(d["jobs"][0])]
    d["jobs"][1]["id"], d["jobs"][1]["title_en"] = "check_seal", "Check the door seal"
    d["jobs"][1]["after"] = [
        {"job_id": "clean_debris_filter", "because_en": "same visit", "page": 3,
         "quote": "Wipe the door seal with a dry cloth after each wash."},
        {"job_id": "clean_debris_filter", "because_en": "planted", "page": 3,
         "quote": "Never wipe the door seal with a dry cloth after each wash."},
    ]
    d["jobs"][0]["due_name_en"] = "filter"
    res = await _build(tmp_path, acme_pdf, d, {"fixes": []})
    assert res.card is not None, res.error
    first, second = res.card["jobs"]
    assert first["due_name"] == {"en": "filter"} and "due_name" not in second
    assert [a["because"]["en"] for a in second["after"]] == ["same visit"]
    assert any(q["id"] == second["after"][0]["quote_ref"] for q in res.card["quotes"])
    assert [r.reason for r in res.rejected] == ["critical_tokens"]
    assert "back panel" in res.card["keywords"]


async def test_every_clause_cites_and_a_witnessed_step_always_has_one(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    steps = d["jobs"][0]["steps"]
    steps[3]["done_when"] = []  # the model gave none, but the step puts a part back: code adds the witness clause
    steps[3]["witness"] = {"part": "debris_filter", "expect": "present", "anchor": None}
    d["jobs"] = d["jobs"][:1]
    res = await _build(tmp_path, acme_pdf, d, None)
    assert res.card is not None, res.error
    quotes = {q["id"]: q for q in res.card["quotes"]}
    got = res.card["jobs"][0]["steps"]
    assert got[3]["done_when"] == [{"id": "debris_filter_present", "text": {"en": "The debris filter is in place."},
                                    "quote_ref": "q_clean_debris_filter_refit"}]
    for s in got:
        for c in s["done_when"]:
            assert quotes[c["quote_ref"]]["text"] == s["quote"]["text"]
            assert quotes[c["quote_ref"]]["page"] == s["quote"]["page"]


def test_a_reused_step_id_is_qualified_by_its_job() -> None:
    d = {"jobs": [{"id": "clean_filters", "steps": [{"id": "remove_filter_system"}, {"id": "s1"}]},
                  {"id": "clean_drain_pump", "steps": [{"id": "Remove filter system"}, {"id": "s1"}]}]}
    af._normalise_ids(d)
    assert [s["id"] for j in d["jobs"] for s in j["steps"]] == [
        "remove_filter_system", "s1", "clean_drain_pump-remove_filter_system", "clean_drain_pump-s1"]
    assert af._GENERIC_STEP_ID.fullmatch("clean_drain_pump-s1")


@pytest.mark.parametrize("clause, ok", [
    ("The arrow marks on the cylindrical filter and coarse filter line up.", False),  # the descale step, Bosch p45
    ("The 2 tabs on the outside of the coarse filter are fully seated.", False),
    ("The appliance is operating with the water softener in use.", True),
])
def test_a_clause_must_be_about_its_own_step(clause: str, ok: bool) -> None:
    step = {"text_en": "Descale the appliance.", "text_manual": "Het apparaat ontkalken.",
            "quote": "Het apparaat ontkalken en met waterontharding gebruiken.", "parts": [], "witness": None}
    assert (af.clause_off_step(clause, step, {"coarse_filter": ["coarse filter", "grove zeef"]}) is None) == ok


async def test_a_job_whose_clause_stays_off_step_is_dropped(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    other = copy.deepcopy(d["jobs"][0])
    other["id"], other["interval"], other["duration"] = "check_debris_filter", None, None
    d["jobs"] = [d["jobs"][0], other]
    d["jobs"][0]["steps"][0]["done_when"] = [{"id": "x", "text_en": "The drum paddles are clean"}]  # on "open_flap"
    res = await _build(tmp_path, acme_pdf, d, None)  # no fixture for the retry: the clause stays off-step
    assert res.card is not None, res.error
    assert [j["id"] for j in res.card["jobs"]] == ["check_debris_filter"]
    got = [(r.reason.split(":")[0], r.dropped) for r in res.rejected]
    assert got == [("clause_off_step", "job clean_debris_filter")]


# ---- polish 21/22: words as a listener hears them ------------------------------------------------------------------


def test_a_word_for_the_whole_appliance_is_never_a_parts_name() -> None:
    # a boiler's casing the draft named "toestel" in Dutch: "Schakel het toestel uit" would be a line about the casing
    assert af.kept_names("casing", "toestel") == ("casing", "")
    assert af.kept_names("appliance", "het apparaat") == ("", "")
    # negative controls: real part names, a word only containing one, and the one-name rule still apply
    assert af.kept_names("boiler casing", "ketelmantel") == ("boiler casing", "ketelmantel")
    assert af.kept_names("control unit cover", "toestelkap") == ("control unit cover", "toestelkap")
    assert af.kept_names("fine sieve / micro sieve", "fijnzeef") == ("fine sieve", "fijnzeef")


def test_one_name_per_part_and_tidy_quotes() -> None:
    assert af.one_name("sieve or filter system") == "sieve"
    assert af.one_name("fine sieve / micro sieve") == "fine sieve"
    assert af.one_name("lid of the rinse aid reservoir") == "lid of the rinse aid reservoir"
    assert af.tidy_quote("het zeefsysteem eruit nemen .") == "het zeefsysteem eruit nemen."
    assert af.tidy_quote("Draai , dan los ;") == "Draai, dan los;"


def test_the_witness_clause_reads_as_a_sentence() -> None:
    present = af.witness_clause({"part": "f", "expect": "present", "anchor": None}, {"f": "filters"})
    absent = af.witness_clause({"part": "c", "expect": "absent", "anchor": "s"}, {"c": "drain cap", "s": "sump"})
    assert present["text"]["en"] == "The filters are in place."  # a check step only looks: not "back in place"
    assert absent["text"]["en"] == "The drain cap is out of the sump."


async def test_a_translated_quote_and_one_job_per_title(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["manual_lang"] = "nl"  # the quotes stay verbatim; quote_en is what an English reader sees under them
    d["parts"][0]["name_en"] = d["parts"][0]["name_en"] + " / filter"
    for s in d["jobs"][0]["steps"]:
        s["quote_en"] = "An English line for the quote."
    twin = copy.deepcopy(d["jobs"][0])
    twin["id"], twin["steps"] = "clean_debris_filter_2", twin["steps"][:1]
    d["jobs"].insert(1, twin)
    res = await _build(tmp_path, acme_pdf, d)
    assert res.card is not None, res.error
    jsonschema.validate(res.card, af.CARD_SCHEMA)
    jobs = res.card["jobs"]
    assert [j["id"] for j in jobs].count("clean_debris_filter_2") == 0 and jobs[0]["id"] == "clean_debris_filter"
    assert any(r.reason.startswith("duplicate_job") and r.dropped == "job clean_debris_filter_2" for r in res.rejected)
    assert all(s["quote"]["en"] == "An English line for the quote." for s in jobs[0]["steps"])
    refs = {q["id"]: q for q in res.card["quotes"]}
    assert all(refs[c["quote_ref"]].get("en") for s in jobs[0]["steps"] for c in s["done_when"])
    assert not any(q["id"].startswith("q_clean_debris_filter_2") for q in res.card["quotes"])
    assert "/" not in res.card["parts"][0]["names"]["en"]


UNPLUG = {"id": "unplug", "text_en": "Unplug the machine.", "text_manual": "Unplug the machine.", "page": 4,
          "quote": "Unplug the machine from the socket before you start.", "done_when": [], "parts": [],
          "power": "off", "zones": [], "view": "front", "look_only_lines": []}
REPAIR_FIXED = {"fixes": [{"key": "jobs[clean_drum].steps[solvent]", "page": 3,
                           "quote": "Do not use solvents to clean the drum."}]}


def _job_repair_fixture(tmp_path: Path, acme_pdf: Path, draft: dict[str, Any], answer: dict[str, Any]) -> None:
    """The job repair's request as the build sends it (the draft as checked, before the clause pass)."""
    client = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    texts = [p.text for p in extract(acme_pdf)]
    d = copy.deepcopy(draft)
    af._normalise_ids(d)
    af.drop_repeated_steps(d)
    for _, o in af._cites(d):  # at the page the citation check finds, as the build reads it
        if (r := verify(texts, int(o["page"]), o["quote"])).ok and r.page is not None:
            o["page"] = r.page
    whole = af.for_repair(d, af.whole_job_failures(d, texts, gate=True, defects=False), texts)
    jobs = [j for j in d["jobs"] if j["id"] in whole]
    body = client.build_respond_body("appliance_file", af.job_repair_input(jobs, whole, texts, d["appliance_kind"]),
                                     instructions=af.JOB_REPAIR_INSTRUCTIONS, format=af.JOB_REPAIR_FORMAT)
    write_respond_fixture(tmp_path, body, responses_events(text=json.dumps(answer), model=WONDER))


async def test_a_job_cut_short_that_the_repair_cannot_fix_is_dropped_and_listed(
        tmp_path: Path, acme_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(af, "RESTORE_APPEND", False)  # without our own restore step
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["steps"].insert(0, copy.deepcopy(UNPLUG))  # unplugged, and the manual never says plug back in
    _job_repair_fixture(tmp_path, acme_pdf, d, {"jobs": [{"key": "clean_debris_filter", "fixable": False,
                                                          "remove": [], "add": []}]})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and [j["id"] for j in res.card["jobs"]] == ["clean_drum"]
    assert [(r.where, r.reason) for r in res.rejected] == [
        ("jobs[clean_debris_filter]", "whole_job: leaves_state: mains off, never back on")]
    assert "clean_debris_filter" in res.rejected[0].dropped and "1 rejected" in res.rail


async def test_a_job_that_only_leaves_the_mains_off_gets_our_own_fixed_step(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["steps"].insert(0, copy.deepcopy(UNPLUG))
    _job_repair_fixture(tmp_path, acme_pdf, d, {"jobs": [{"key": "clean_debris_filter", "fixable": False,
                                                          "remove": [], "add": []}]})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and not res.rejected
    job = res.card["jobs"][0]
    assert [st["id"] for st in job["steps"]] == ["unplug", "open_flap", "wait_drum_empty", "drain", "refit",
                                                 "clean_debris_filter_restore_mains"]
    ours = job["steps"][-1]
    # fixed words from the table, in English and the manual's language, never the model's; no page, no quote
    assert ours["text"] == {"en": af.RESTORE_TEXT["mains"]["en"]}
    assert (ours["origin"], ours["added_by"], ours["restores"], ours["page"], ours["quote"]) == (
        "fixit", "ingest", "unplug", None, None)
    assert (ours["power"], ours["done_when"], ours["parts"], ours["zones"]) == ("any", [], [], [])
    assert contracts.card_invariants(res.card) == []


async def test_a_job_failing_for_any_other_reason_gets_no_step_of_ours(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    # unplugged and never plugged back in, and it also points outside the job: not ours to finish
    d["jobs"][0]["steps"].insert(0, copy.deepcopy(UNPLUG))
    d["jobs"][0]["steps"].insert(3, {**copy.deepcopy(d["jobs"][0]["steps"][2]), "id": "again",
                                     "text_en": "Repeat the procedure for the second filter.",
                                     "quote": "Rinse the filter under running water and remove any fluff."})
    _job_repair_fixture(tmp_path, acme_pdf, d, {"jobs": [{"key": "clean_debris_filter", "fixable": False,
                                                          "remove": [], "add": []}]})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and [j["id"] for j in res.card["jobs"]] == ["clean_drum"]
    [why] = [r.reason for r in res.rejected]
    assert "leaves_state: mains off, never back on" in why and "refers_outside" in why
    assert not any(s.get("origin") == "fixit" for j in res.card["jobs"] for s in j["steps"])


async def test_a_step_of_ours_drafted_by_the_model_is_dropped(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["steps"].append({"id": "plug_in", "origin": "fixit", "restores": "refit",
                                  "text_en": "Plug it in and run a rinse.", "text_manual": "Plug it in.",
                                  "page": 4, "quote": ""})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None
    assert "plug_in" not in {s["id"] for j in res.card["jobs"] for s in j["steps"]}
    assert ("jobs[clean_debris_filter].steps[plug_in]", "drafted_fixit: only code adds our own step") in {
        (r.where, r.reason) for r in res.rejected}


async def test_the_repair_takes_out_a_step_that_points_outside_the_job(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["steps"].insert(2, {**copy.deepcopy(d["jobs"][0]["steps"][1]), "id": "again",
                                     "text_en": "Repeat the procedure for the second filter.",
                                     "quote": "Rinse the filter under running water and remove any fluff."})
    _job_repair_fixture(tmp_path, acme_pdf, d, {"jobs": [{"key": "clean_debris_filter", "fixable": True,
                                                          "remove": [3], "add": []}]})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and not res.rejected
    job = res.card["jobs"][0]
    assert [st["id"] for st in job["steps"]] == ["open_flap", "wait_drum_empty", "drain", "refit"]


async def test_a_warning_the_draft_dropped_goes_to_the_job_repair_and_the_job_is_kept(tmp_path: Path,
                                                                                      acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    warning = d["jobs"][0]["steps"].pop(1)  # the manual's "do not" line between opening the flap and draining
    _job_repair_fixture(tmp_path, acme_pdf, d, {"jobs": [{"key": "clean_debris_filter", "fixable": True,
                                                          "remove": [], "add": [{"before": 2, "step": warning}]}]})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and not res.rejected
    assert [st["id"] for st in res.card["jobs"][0]["steps"]] == ["open_flap", "wait_drum_empty", "drain", "refit"]


async def test_the_repair_mends_the_warning_and_code_alone_plugs_the_machine_back_in(tmp_path: Path,
                                                                                     acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["steps"].insert(0, copy.deepcopy(UNPLUG))  # unplugged, and the manual never says plug back in
    warning = d["jobs"][0]["steps"].pop(2)
    # the repair is asked about the warning only: a "mains off" our own step undoes would make it give the job up
    texts = [p.text for p in extract(acme_pdf)]
    checked = copy.deepcopy(d)
    for _, o in af._cites(checked):
        if (r := verify(texts, int(o["page"]), o["quote"])).ok and r.page is not None:
            o["page"] = r.page
    asked = af.for_repair(checked, af.whole_job_failures(checked, texts, gate=True, defects=False), texts)
    assert [f.code for f in asked["clean_debris_filter"]] == ["warning_dropped"]
    _job_repair_fixture(tmp_path, acme_pdf, d, {"jobs": [{"key": "clean_debris_filter", "fixable": True,
                                                          "remove": [], "add": [{"before": 3, "step": warning}]}]})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and not res.rejected
    steps = res.card["jobs"][0]["steps"]
    assert [st["id"] for st in steps][1:4] == ["open_flap", "wait_drum_empty", "drain"]
    assert steps[-1]["id"] == "clean_debris_filter_restore_mains" and steps[-1]["origin"] == "fixit"


async def test_a_repair_that_still_fails_the_whole_job_check_is_dropped(tmp_path: Path, acme_pdf: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(af, "RESTORE_APPEND", False)  # without our own restore step
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["steps"].insert(0, copy.deepcopy(UNPLUG))
    # the proposal adds a step, but not one that plugs the machine back in
    rinse = {**copy.deepcopy(d["jobs"][0]["steps"][2]), "id": "rinse", "text_en": "Rinse the filter.",
             "quote": "Rinse the filter under running water and remove any fluff."}
    _job_repair_fixture(tmp_path, acme_pdf, d, {"jobs": [{"key": "clean_debris_filter", "fixable": True,
                                                          "remove": [], "add": [{"before": 4, "step": rinse}]}]})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and [j["id"] for j in res.card["jobs"]] == ["clean_drum"]
    assert res.rejected[0].reason == "whole_job: leaves_state: mains off, never back on"


async def test_a_repeated_step_is_dropped_by_code_and_the_job_kept(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    flap = d["jobs"][0]["steps"][0]
    d["jobs"][0]["steps"].insert(1, {**copy.deepcopy(flap), "id": "flap_again", "text_en": "Open the filter flap now."})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and [st["id"] for st in res.card["jobs"][0]["steps"]] == [
        "open_flap", "wait_drum_empty", "drain", "refit"]


def _title_fixture(tmp_path: Path, draft: dict[str, Any], answer: dict[str, Any]) -> None:
    """The title call's request as the build sends it: the jobs whose title code could not make a plain task."""
    client = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    d = copy.deepcopy(draft)
    af._normalise_ids(d)
    for j in d["jobs"]:
        af.pick_title(j, None, brand="acme", model="WM-0000", lang=d["manual_lang"])
    jobs = [j for j in d["jobs"]
            if af.title_problems(j["title_en"], j["title_manual"], brand="acme", model="WM-0000", lang="en")]
    body = client.build_respond_body("appliance_file", af.title_input(jobs, d["manual_lang"]),
                                     instructions=af.TITLE_INSTRUCTIONS, format=af.TITLE_FORMAT, effort="low")
    write_respond_fixture(tmp_path, body, responses_events(text=json.dumps(answer), model=WONDER))


async def test_a_title_that_names_the_model_is_replaced_by_a_checked_plain_task(tmp_path: Path,
                                                                                acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["title_en"] = d["jobs"][0]["title_manual"] = "Debris filter upkeep (new) WM-0000"
    _title_fixture(tmp_path, d, {"jobs": [{"key": "clean_debris_filter", "title_en": "Clean the debris filter",
                                           "title_manual": "Clean the debris filter"}]})
    res = await _build(tmp_path, acme_pdf, d, REPAIR_FIXED)
    assert res.card is not None and res.card["jobs"][0]["title"] == {"en": "Clean the debris filter"}


def test_a_proposed_title_is_taken_only_when_it_passes_the_same_check() -> None:
    bad = {"title_en": "Debris filter upkeep (new) WM-0000", "title_manual": "Vuilfilter onderhoud (nieuw) WM-0000"}
    for alt, want in [
        ({"title_en": "Acme debris filter (WM-0000)", "title_manual": "Vuilfilter"}, ("Debris filter upkeep",
                                                                                      "Vuilfilter onderhoud")),
        ({"title_en": "Clean the debris filter / sieve", "title_manual": "Vuilfilter reinigen"},
         ("Debris filter upkeep", "Vuilfilter onderhoud")),  # a slash code cannot take out: the pair is refused
        ({"title_en": "Clean the debris filter", "title_manual": "Vuilfilter reinigen (nieuw)"},
         ("Clean the debris filter", "Vuilfilter reinigen")),  # a note code can take out: cleaned, and taken
    ]:
        j = dict(bad)
        af.pick_title(j, alt, brand="acme", model="WM-0000", lang="nl")
        assert (j["title_en"], j["title_manual"]) == want
    good = {"title_en": "Clean the debris filter (fault E20)", "title_manual": "Vuilfilter reinigen (storing E20)"}
    af.pick_title(good, {"title_en": "Clean it", "title_manual": "Reinigen"}, brand="acme", model="WM-0000", lang="nl")
    assert good["title_en"] == "Clean the debris filter (fault E20)"  # a passing draft title is never replaced


def test_only_the_cards_language_pages_and_the_figures_are_kept() -> None:
    en, de = "the and is of to with " * 3, "der die das und ist mit " * 3
    texts = [de, en, "", en, en, de, en, de]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(af, "page_lang", lambda t: "en" if t == en else "de" if t == de else None)
        assert af.language_pages(texts, "en") == [2, 3, 4, 5, 7]
        assert af.language_pages(texts[:6], "de") is None  # 2 pages of German is not a manual: the whole PDF
        assert af.language_pages([en, en, en, en, ""], "en") is None  # nothing to leave out
    assert af.page_back([2, 3, 4, 5, 7], 5) == 7 and af.page_back([2, 3, 4, 5, 7], 9) == 9
    assert af.page_back(None, 4) == 4


async def test_a_draft_from_the_language_pages_cites_the_manuals_own_pages(tmp_path: Path, acme_pdf: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Page 1 in another language: the draft reads pages 2-6 as its 1-5, and the card cites the manual's 2-6."""
    first = extract(acme_pdf)[0].text
    monkeypatch.setattr(af, "page_lang", lambda t: "de" if t == first else "en")
    keep = af.language_pages([p.text for p in extract(acme_pdf)], "en")
    assert keep == [2, 3, 4, 5, 6]
    sent = af.pdf_pages(acme_pdf, keep)
    assert sent == af.pdf_pages(acme_pdf, keep)  # the same bytes every time: a replayed request matches
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][:1]
    for _, o in af._cites(d):
        o["page"] -= 1
    client = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    body = client.build_respond_body("appliance_file", af.request_input(sent, acme_pdf.name),
                                     instructions=af.INSTRUCTIONS, format=af.FILE_FORMAT)
    write_respond_fixture(tmp_path, body, responses_events(text=json.dumps(d), model=WONDER))
    res = await af.build(acme_pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme",
                         country="xx", client=client)
    assert res.card is not None, res.error
    assert [s["page"] for s in res.card["jobs"][0]["steps"]] == [4, 4, 4, 4]
    assert res.card["red_zones"][0]["page"] == 2
    # the planted wrong page (DRAFT's open_flap) is moved from the manual's 3, not the draft's 2
    assert [(f, t) for _, f, t in res.corrected] == [(3, 4)]


def _row(rid: str, codes: list[str], quote: str, *, kind: str = "user", job: str = "", page: int = 5,
         meaning: str = "The water does not drain.", remedy: str = "Clean the debris filter.") -> dict[str, Any]:
    blank = {f"{f}_{w}": "" for f in ("indicator", "symptom", "cause") for w in ("en", "manual")}
    return {"id": rid, "codes": codes, **blank, "meaning_en": meaning, "meaning_manual": meaning,
            "remedy_en": remedy, "remedy_manual": remedy, "remedy_kind": kind,
            "escalate_to": "technician" if kind == "user" else "none", "job_id": job, "prevented_by": [],
            "repeat": None, "page": page, "quote": quote, "quote_en": quote}


def _draft_with_table() -> dict[str, Any]:
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][:1]
    d["troubleshooting"] = [
        _row("e21", ["E21"], "E21: the water does not drain. Clean the debris filter.", job="clean_debris_filter"),
        _row("e99", ["E99"], "E99: do not try to repair this yourself. Call a technician.", kind="technician",
             job="clean_debris_filter", meaning="A fault you must not repair.", remedy="Call a technician."),
        _row("drain_word", ["Drain"], "E21: the water does not drain. Clean the debris filter."),
        _row("invented", ["E42"], "E42: the door is not shut. Close the door firmly."),
    ]
    d["models_listed"] = [{"code": "WM-0000", "page": 1, "quote": "ACME WM-0000 WASHING MACHINE"},
                          {"code": "Acme", "page": 1, "quote": "ACME WM-0000 WASHING MACHINE"}]
    d["upkeep_procedures"] = [
        {"heading": "Cleaning the debris filter", "page": 4, "job_id": "clean_debris_filter", "drop_reason": ""},
        {"heading": "Care and maintenance", "page": 3, "job_id": "", "drop_reason": ""},
        {"heading": "Technical data", "page": 6, "job_id": "", "drop_reason": "operating_not_upkeep"},
        {"heading": "Descaling the heater", "page": 4, "job_id": "", "drop_reason": ""},
        {"heading": "Fault codes", "page": 3, "job_id": "", "drop_reason": "technician_only"},
    ]
    return d


async def test_the_fault_table_becomes_cited_rows_and_a_technicians_remedy_is_kept(tmp_path: Path,
                                                                                  acme_pdf: Path) -> None:
    d = _draft_with_table()
    failed = [("troubleshooting[invented]", d["troubleshooting"][3])]
    client = _client(tmp_path, acme_pdf, d, None)
    rb = client.build_respond_body("appliance_file", af.repair_input(failed, [p.text for p in extract(acme_pdf)]),
                                   instructions=af.REPAIR_INSTRUCTIONS, format=af.REPAIR_FORMAT, effort="low")
    write_respond_fixture(tmp_path, rb, responses_events(text=json.dumps(
        {"fixes": [{"key": "troubleshooting[invented]", "page": 5, "quote": None}]}), model=WONDER))
    res = await af.build(acme_pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme",
                         country="xx", client=client)
    assert res.card is not None, res.error
    jsonschema.validate(res.card, af.CARD_SCHEMA)
    rows = {r["id"]: r for r in res.card["troubleshooting"]}
    assert set(rows) == {"e21", "e99"}  # the word code has nothing left; the invented line fails its quote
    assert rows["e21"]["codes"] == ["E21"] and rows["e21"]["escalate_to"] == "technician"
    assert rows["e21"]["job_ref"] is None  # the job that cleans the filter is upkeep, and job_ref names a fix job
    assert (rows["e99"]["remedy_kind"], rows["e99"]["job_ref"], rows["e99"]["escalate_to"]) == ("technician", None,
                                                                                                None)
    quotes = {q["id"]: q for q in res.card["quotes"]}
    assert quotes[rows["e99"]["quote_ref"]]["page"] == 5
    reasons = {r.where: r.reason for r in res.rejected}
    assert reasons["troubleshooting[drain_word].codes"] == "fault_code: no digit: not a code"
    assert reasons["troubleshooting[drain_word]"] == "troubleshooting: no code, indicator or symptom"
    assert "troubleshooting[invented]" in reasons
    assert res.models == ["WM-0000"]  # the maker's name is not a model code
    assert [(c["heading"], c["status"]) for c in res.coverage] == [
        ("Cleaning the debris filter", "job"), ("Care and maintenance", "missing"),
        ("Technical data", "not_upkeep"), ("Fault codes", "not_upkeep")]  # a heading not printed is left out
    # a technician's procedure is on the card by name and page, at the page that prints its heading
    # and the procedure with no job, by its own heading
    assert res.card["refused"] == [{"id": "fault_codes", "title": {"en": "Fault codes"},
                                    "reason": "needs_professional", "page": 5},
                                   {"reason": "not_built", "heading": "Care and maintenance", "page": 3}]


def test_rows_and_jobs_get_unique_ids_before_the_citation_check() -> None:
    """Two rows under the model's placeholder id once shared one citation result, and so one page."""
    d = _draft_with_table()
    for r in d["troubleshooting"]:
        r["id"] = "autogen"
    d["troubleshooting"][2]["codes"] = []
    d["troubleshooting"][2]["symptom_en"] = "Water on the floor"
    d["jobs"].append(copy.deepcopy(d["jobs"][0]))
    af._normalise_ids(d)
    assert [r["id"] for r in d["troubleshooting"]] == ["fault_e21", "fault_e99", "water_on_the_floor", "fault_e42"]
    assert [j["id"] for j in d["jobs"]] == ["clean_debris_filter", "clean_debris_filter_2"]
    assert len({w for w, _ in af._cites(d)}) == len(af._cites(d))


def test_an_empty_interval_duration_or_repeat_is_none_not_a_failed_quote() -> None:
    d = _draft_with_table()
    d["jobs"][0]["interval"] = {"every": 0, "unit": "months", "page": 0, "quote": ""}
    d["jobs"][0]["duration"] = {"minutes": 0, "page": 0, "quote": "  "}
    d["troubleshooting"][0]["repeat"] = {"count": 0, "days": 0, "page": 0, "quote": ""}
    af._normalise_ids(d)
    assert d["jobs"][0]["interval"] is None and d["jobs"][0]["duration"] is None
    assert d["troubleshooting"][0]["repeat"] is None
    assert not [w for w, _ in af._cites(d) if w.endswith((".interval", ".duration", ".repeat"))]


def test_our_restore_step_reads_the_same_fixed_words_in_dutch() -> None:
    d = {"manual_lang": "nl", "jobs": [{"id": "pomp", "title_en": "Clean the pump", "title_manual": "", "steps": [
        {"id": "stekker", "text_en": "Unplug the machine.", "text_manual": "Trek de stekker uit het stopcontact.",
         "page": 1, "quote": "Trek de stekker uit het stopcontact.", "power": "off"},
        {"id": "filter", "text_en": "Clean the pump filter.", "text_manual": "Reinig het pompfilter.", "page": 1,
         "quote": "Reinig het pompfilter onder de kraan.", "power": "off"}]}]}
    texts = ["Trek de stekker uit het stopcontact.\nReinig het pompfilter onder de kraan.\n"]
    assert af.append_restores(d, af.whole_job_failures({**d, "appliance_kind": "washer"}, texts), texts) == ["pomp"]
    ours = d["jobs"][0]["steps"][-1]
    assert (ours["text_en"], ours["text_manual"]) == ("Plug the appliance back in.",
                                                      "Steek de stekker weer in het stopcontact.")
    # a step that turns the mains off without power "off" can not be restored by ours (the contract's rule)
    d["jobs"][0]["steps"] = d["jobs"][0]["steps"][:2]
    d["jobs"][0]["steps"][0]["power"] = "any"
    assert af.append_restores(d, af.whole_job_failures({**d, "appliance_kind": "washer"}, texts), texts) == []


def _rack_draft() -> tuple[dict[str, Any], list[str]]:
    d = {"manual_lang": "nl", "appliance_kind": "dishwasher", "red_zones": [], "parts": [
        {"id": "onderrek", "name_manual": "onderrek", "name_en": "lower rack", "kind": "allowed"},
        {"id": "zeef", "name_manual": "zeef", "name_en": "filter", "kind": "allowed"}],
        "jobs": [{"id": "zeven", "title_en": "Clean the filters", "title_manual": "", "steps": [
            {"id": "uit", "text_en": "Switch the dishwasher off.", "text_manual": "Schakel de vaatwasser uit.",
             "page": 1, "quote": "Schakel de vaatwasser uit.", "power": "off", "parts": [], "zones": []},
            {"id": "rek", "text_en": "Pull the lower rack out.", "text_manual": "Trek het onderrek naar buiten.",
             "page": 1, "quote": "Trek het onderrek naar buiten.", "power": "off", "parts": ["onderrek"],
             "zones": []},
            {"id": "spoel", "text_en": "Rinse the filter.", "text_manual": "Spoel de zeef af.", "page": 1,
             "quote": "Spoel de zeef onder stromend water af.", "power": "off", "parts": ["zeef"], "zones": []}]}]}
    return d, ["Schakel de vaatwasser uit.\nTrek het onderrek naar buiten.\nSpoel de zeef onder stromend water af.\n"]


def test_our_step_puts_back_a_part_the_manual_never_does_in_fixed_words() -> None:
    d, texts = _rack_draft()
    assert af.append_restores(d, af.whole_job_failures(d, texts, gate=True), texts) == ["zeven"]
    ours = d["jobs"][0]["steps"][-1]
    assert (ours["origin"], ours["added_by"], ours["restores"], ours["page"], ours["quote"]) == (
        "fixit", "ingest", "rek", 0, "")
    assert (ours["text_en"], ours["text_manual"]) == ("Put the lower rack back in place.", "Plaats terug: onderrek.")
    assert af.whole_job_failures(d, texts, gate=True) == {}
    # a part a red zone holds is never ours to put back
    d, texts = _rack_draft()
    d["red_zones"] = [{"id": "z", "part_id": "onderrek", "rule": "water.aquastop", "page": 1, "quote": ""}]
    assert af.append_restores(d, af.whole_job_failures(d, texts, gate=True), texts) == []


def test_our_plug_in_goes_just_before_the_step_that_runs_the_machine() -> None:
    d: dict[str, Any] = {"manual_lang": "nl", "appliance_kind": "washer", "red_zones": [], "parts": [], "jobs": [
        {"id": "pomp", "title_en": "Clean the pump", "title_manual": "", "steps": [
            {"id": "stekker", "text_en": "Unplug the machine.", "text_manual": "Trek de stekker uit het stopcontact.",
             "page": 1, "quote": "Trek de stekker uit het stopcontact.", "power": "off", "parts": [], "zones": []},
            {"id": "filter", "text_en": "Clean the pump filter.", "text_manual": "Reinig het pompfilter.", "page": 1,
             "quote": "Reinig het pompfilter onder de kraan.", "power": "off", "parts": [], "zones": []},
            {"id": "afpompen", "text_en": "Start the Drain programme.", "text_manual": "Start het programma Afpompen.",
             "page": 1, "quote": "Start het programma Afpompen.", "power": "on", "parts": [], "zones": []}]}]}
    texts = ["Trek de stekker uit het stopcontact.\nReinig het pompfilter onder de kraan.\n"
             "Start het programma Afpompen.\n"]
    assert af.append_restores(d, af.whole_job_failures(d, texts, gate=True), texts) == ["pomp"]
    steps = d["jobs"][0]["steps"]
    assert [st["id"] for st in steps] == ["stekker", "filter", "pomp_restore_mains", "afpompen"]
    assert steps[2]["restores"] == "stekker"
    assert af.whole_job_failures(d, texts, gate=True) == {}


def test_a_long_fault_line_is_quoted_by_its_first_two_sentences_and_a_long_step_still_fails() -> None:
    d = _draft_with_table()
    d["troubleshooting"][0]["quote"] = ("E21: the water does not drain. Clean the debris filter. Check the hose. "
                                        "Call the service.")
    d["jobs"][0]["steps"][0]["quote"] = "One. Two. Three."
    af.short_row_quotes(d)
    assert d["troubleshooting"][0]["quote"] == "E21: the water does not drain. Clean the debris filter."
    assert d["jobs"][0]["steps"][0]["quote"] == "One. Two. Three."


@pytest.mark.parametrize(("quote", "every", "unit"), [
    ("Reinig het filter twee keer per jaar.", 6, "month"),
    ("Clean the filter twice a year.", 6, "month"),
    ("Den Filter 4 mal im Jahr reinigen.", 3, "month"),
    ("Reinig de pomp 2 keer per maand.", 2, "week"),
    ("Clean the filter every 2 years.", 2, "year"),  # a real step between is left as drafted
])
def test_times_per_year_is_never_read_as_every_n_years(quote: str, every: int, unit: str) -> None:
    iv = {"every": 2, "unit": "year", "page": 1, "quote": quote}
    af.per_period(iv)
    assert (iv["every"], iv["unit"]) == (every, unit)


async def test_a_procedure_the_build_could_not_turn_into_a_job_is_named_by_its_own_heading(
        tmp_path: Path, acme_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(af, "RESTORE_APPEND", False)  # so the recorded card's filter job is dropped
    pdf = tmp_path / "WM-0000.pdf"
    pdf.write_bytes(acme_pdf.read_bytes())
    client = calls.make_client("replay", fixtures_root=calls.FIXTURES, private=None)
    res = await af.build(pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme/wm-0000",
                         country="xx", client=client)
    assert res.card is not None, res.error
    # the manual's headings as printed, with their pages; no id, no title, never steps
    assert res.card["refused"] == [{"reason": "not_built", "heading": "3 Cleaning the debris filter", "page": 4},
                                   {"reason": "not_built", "heading": "4 Fault codes", "page": 5}]
    assert contracts.card_invariants(res.card) == []


def test_a_job_refused_by_name_is_not_listed_again_as_not_built() -> None:
    cov = [{"heading": "Gaskraan  sluiten", "page": 9, "job_id": "close_gas", "status": "dropped", "reason": "x"},
           {"heading": "Filter reinigen", "page": 4, "job_id": None, "status": "missing", "reason": "x"},
           {"heading": "filter reinigen", "page": 5, "job_id": None, "status": "missing", "reason": "x"},
           {"heading": "Vorst", "page": 6, "job_id": None, "status": "not_upkeep", "reason": "x"}]
    refused = [{"id": "close_gas", "title": {"nl": "Gaskraan sluiten"}, "reason": "gas", "page": 9}]
    assert af.not_built(cov, refused) == [{"reason": "not_built", "heading": "Filter reinigen", "page": 4}]


def _heat_card(*, cool_page: int | None = 49, rule: str = "nl.heat.hot_water") -> dict[str, Any]:
    def st(sid: str, page: int, text: str, parts: list[str]) -> dict[str, Any]:
        return {"id": sid, "page": page, "quote": {"text": text, "lang": "nl", "page": page}, "parts": parts,
                "zones": ["hose_hot"] if "drain_hose" in parts else []}
    steps = [st("unplug", 48, "Trek de stekker uit het stopcontact.", []),
             st("hose_out", 49, "Neem de aftapslang uit de houder.", ["drain_hose"]),
             st("hose_back", 50, "De aftapslang in de houder klemmen.", ["drain_hose"])]
    if cool_page is not None:
        steps.insert(1, st("cool", cool_page, "Laat het sop eerst afkoelen.", []))
    return {"parts": [{"id": "drain_hose", "kind": "red_zone"}, {"id": "door", "kind": "red_zone"}],
            "red_zones": [{"id": "hose_hot", "part_id": "drain_hose", "rule_id": rule},
                          {"id": "door_hot", "part_id": "door", "rule_id": "nl.heat.hot_water"}],
            "jobs": [{"id": "clean_drain_pump", "steps": steps}]}


def test_a_heat_zone_is_lifted_only_where_the_job_cites_the_manuals_own_cool_down() -> None:
    assert af.HEAT_LIFT is False and af.instructions() == af.INSTRUCTIONS  # off until reviewed
    card = _heat_card()
    lifts = af.heat_lifts(card)
    # recorded for the panel: "hot-water zone on the drain hose lifted: the job carries the manual's cool-down, p.49"
    assert lifts == [{"zone": "hose_hot", "rule_id": "nl.heat.hot_water", "part_id": "drain_hose",
                      "jobs": [{"job": "clean_drain_pump", "step": "cool", "page": 49}]}]
    af.lift_heat(card, lifts)
    assert [z["id"] for z in card["red_zones"]] == ["door_hot"]  # no job works on the door: it stays
    assert all("hose_hot" not in s["zones"] for s in card["jobs"][0]["steps"])
    assert {p["id"]: p["kind"] for p in card["parts"]} == {"drain_hose": "allowed", "door": "red_zone"}


@pytest.mark.parametrize("card", [
    _heat_card(cool_page=None),  # negative control: the same job without the cool-down keeps the zone
    _heat_card(cool_page=12),  # a cool-down from another part of the manual is not this procedure's
    _heat_card(rule="nl.mains.casing"),  # mains, gas, water, pressure: never
    _heat_card(rule="nl.gas.tap"),
])
def test_a_heat_zone_stays_without_the_procedures_own_cool_down_and_other_zones_never_lift(
        card: dict[str, Any]) -> None:
    assert af.heat_lifts(card) == []


def test_one_job_on_the_part_without_the_cool_down_keeps_the_zone() -> None:
    card = _heat_card()
    other = copy.deepcopy(card["jobs"][0])
    other["id"], other["steps"] = "check_hose", [s for s in other["steps"] if s["id"] != "cool"]
    card["jobs"].append(other)
    assert af.heat_lifts(card) == []


def _job(jid: str, *steps: dict[str, Any], title: str = "Clean the filter") -> dict[str, Any]:
    return {"id": jid, "title_en": title, "title_manual": title, "steps": list(steps)}


def _step(sid: str, text: str, *clauses: tuple[str, str], power: str = "any") -> dict[str, Any]:
    return {"id": sid, "text_en": text, "text_manual": text, "power": power,
            "done_when": [{"id": c, "text_en": t} for c, t in clauses]}


def test_what_code_can_see_is_wrong_in_a_job_fails_it() -> None:
    good = _job("clean_filter", _step("rinse", "Rinse the filter.", ("clean", "The filter is clean")))
    assert af.job_defects({"jobs": [good]}) == {}
    bad = [_job("a", _step("rinse", "Rinse the filter.", ("x", "The filter is clean"), ("x", "No fluff is left"))),
           _job("b", _step("rinse", "Rinse the seal.", ("y", "You are done."))),
           _job("c", _step("run", "Start the drum cleaning programme.", power="off")),
           _job("d", _step("s1", "clean_filter")),
           _job("e", _step("s2", "Wipe the door."), title="clean_door")]
    got = {j: [f.code + ": " + f.detail for f in fs] for j, fs in af.job_defects({"jobs": bad}).items()}
    assert got == {"a": ["duplicate_id: step 1's id is another step's too",
                         "duplicate_id: two clauses of step 1 share an id"],
                   "b": ["duplicate_id: step 1's id is another step's too",
                         "empty_clause: a clause of step 1 says nothing"],
                   "c": ["power_off_run: step 1 runs a programme with the power off"],
                   "d": ["id_as_text: step 1 shows an id as text"],
                   "e": ["id_as_text: the title is an id"]}
    # our own restore step may be power off whatever it says; a step that only switches off is not a run
    ours = _job("f", {**_step("plug", "Start the programme again.", power="off"), "origin": "fixit"},
                _step("off", "Switch the machine off.", power="off"))
    assert af.job_defects({"jobs": [ours]}) == {}


def test_an_id_used_twice_on_the_card_fails_the_build() -> None:
    card = {"jobs": [{"id": "j", "steps": [{"id": "s"}]}, {"id": "k", "steps": [{"id": "s"}]}],
            "quotes": [{"id": "q"}, {"id": "q"}], "parts": [{"id": "p"}], "red_zones": [], "troubleshooting": []}
    assert af.card_duplicate_ids(card) == ["step id 's' is used 2 times", "quote id 'q' is used 2 times"]


def test_a_maintenance_chapter_is_found_by_its_own_heading_line() -> None:
    pages = ["Contents\nCleaning and maintenance ... 12", "Safety", "7 Reiniging en onderhoud\nDe filter",
             "Cleaning\n"]
    assert af.maintenance_chapter(pages, None) == 3  # a contents line is not the chapter
    assert af.maintenance_chapter(pages, [1, 2, 4]) == 4  # only the pages the draft read
    assert af.maintenance_chapter(pages[:2], None) is None


async def test_a_draft_that_lists_no_procedure_of_a_manual_with_a_maintenance_chapter_fails(
        tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"], d["upkeep_procedures"] = d["jobs"][:1], []
    res = await _build(tmp_path, acme_pdf, d, None)
    assert res.card is None
    assert res.error == ("ValueError: the manual has a maintenance chapter (page 3) and the draft listed none of "
                         "its procedures")


async def test_quote_ids_stay_unique_where_long_ids_would_cut_to_the_same_text(tmp_path: Path,
                                                                                acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][:1] + copy.deepcopy(d["jobs"][:1])
    d["jobs"][0]["id"] = "clean_the_debris_filter_of_the_washing_machine_after_every_long_wash_a"
    d["jobs"][1]["id"] = "clean_the_debris_filter_of_the_washing_machine_after_every_long_wash_b"
    d["jobs"][1]["title_en"] = d["jobs"][1]["title_manual"] = "Clean the debris filter again"
    for st in d["jobs"][1]["steps"]:
        st["id"] += "_b"
    res = await _build(tmp_path, acme_pdf, d, None)
    assert res.card is not None, res.error
    ids = [q["id"] for q in res.card["quotes"]]
    assert len(ids) == len(set(ids)) and af.card_duplicate_ids(res.card) == []


async def test_a_job_on_a_part_under_a_mains_zone_is_refused_and_the_zone_kept(tmp_path: Path, acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][:1]
    d["jobs"][0]["steps"][3]["parts"] = ["debris_filter", "back_panel"]  # the builder put the casing on its part
    res = await _build(tmp_path, acme_pdf, d, None)
    assert res.card is None  # its only job refused: no card, and never one without the zone
    d["jobs"].append({**copy.deepcopy(DRAFT["jobs"][0]), "id": "clean_debris_filter_again",
                      "title_en": "Clean the debris filter again", "title_manual": "Clean the debris filter again"})
    for st in d["jobs"][1]["steps"]:
        st["id"] += "_again"
    res = await _build(tmp_path / "two", acme_pdf, d, None)
    assert res.card is not None, res.error
    assert [z["id"] for z in res.card["red_zones"]] == ["casing"]
    assert [j["id"] for j in res.card["jobs"]] == ["clean_debris_filter_again"]
    assert [(r["id"], r["reason"], r["page"]) for r in res.card["refused"]] == [("clean_debris_filter", "mains", 4)]
    assert [(r.where, r.reason) for r in res.rejected] == [
        ("jobs[clean_debris_filter]",
         "zone_conflict: a step works on back_panel, under red zone casing (xx.mains.casing)")]


def test_a_heat_zone_or_our_own_step_is_no_zone_conflict() -> None:
    heat = {"id": "element", "part_id": "heater", "rule_id": "xx.heat.hot_water"}
    mains = {"id": "casing", "part_id": "panel", "rule_id": "xx.mains.casing"}
    job = {"steps": [{"parts": ["heater"]}, {"parts": ["panel"], "origin": "fixit"}]}
    assert af.zone_conflict(job, [heat, mains]) is None
    assert af.zone_conflict({"steps": [{"parts": ["panel"]}]}, [heat, mains]) == mains
    assert af.zone_reason({"rule_id": "xx.gas.tap"}) == "gas" and af.zone_reason(
        {"rule_id": "xx.water.aquastop"}) == "other"


def test_the_checklist_only_asks_and_code_sorts_each_answer() -> None:
    lists = {"washer": [
        {"id": "washer.pump", "names": {"en": "Clean the pump filter"}, "evidence": 7},
        {"id": "washer.inlet", "names": {"en": "Clean the inlet filters"}, "evidence": 6},
        {"id": "washer.nozzle", "names": {"en": "Clean the nozzle"}, "evidence": 1},
        {"id": "washer.frost", "names": {"en": "Protect against frost"}, "evidence": 3},
        {"id": "washer.descale", "names": {"en": "Descale"}, "evidence": 4},
        {"id": "washer.drawer", "names": {"en": "Clean the drawer"}, "evidence": 7}]}
    pages = ["Cleaning the pump filter\nOpen the flap.", "Inlet filters\nClose the tap. Nozzle in the seal\nRinse it."]
    draft = {"appliance_kind": "washer", "checklist": [
        {"item": "washer.pump", "in_manual": True, "heading": "Cleaning the pump filter", "page": 1, "job_id": "pump"},
        {"item": "washer.inlet", "in_manual": True, "heading": "Inlet filters", "page": 2, "job_id": ""},
        {"item": "washer.nozzle", "in_manual": True, "heading": "Nozzle in the seal", "page": 2, "job_id": ""},
        {"item": "washer.frost", "in_manual": False, "heading": "", "page": 0, "job_id": ""},
        {"item": "washer.descale", "in_manual": True, "heading": "Descaling the machine", "page": 2, "job_id": ""}]}
    card = {"jobs": [{"id": "pump"}], "refused": []}
    got = af.checklist_coverage(draft, card, [], pages, lists)
    assert [(c["item"], c["status"], c["page"]) for c in got] == [
        ("washer.pump", "job", 1),
        ("washer.inlet", "missing", 2),  # the manual gives it and no job does: a gap, with the heading as printed
        ("washer.nozzle", "weak", 2),  # only one manual of the list's holds it: asked about, never a gap
        ("washer.frost", "not_in_manual", None),  # a line, never a job
        ("washer.descale", "unchecked", None),  # said to be there, but that heading is not on that page
        ("washer.drawer", "unchecked", None)]  # no answer
    assert af.not_built(got, card["refused"]) == [{"reason": "not_built", "heading": "Inlet filters", "page": 2}]


def test_the_checklists_hold_names_only_one_per_kind() -> None:
    lists = af.checklists()
    assert set(lists) <= set(af.KINDS) and {"washer", "dishwasher", "boiler"} <= set(lists)
    for kind, items in lists.items():
        assert items and all(i["id"].startswith(f"{kind}.") and i["who"] == "user" for i in items)
        assert all(set(i) <= {"id", "names", "kind", "who", "note", "evidence"} for i in items)  # no gold refs
        assert all(i["id"] in af.INSTRUCTIONS for i in items)  # the reader is asked about every one


def test_each_failing_job_is_repaired_with_its_own_pages_and_the_warnings_page_first(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(af, "MAX_REPAIR_PAGES", 4)
    texts = [f"Page {n} of an invented manual." for n in range(1, 41)]
    early = {"id": "early", "steps": [{"page": 2}, {"page": 3}]}
    late = {"id": "late", "steps": [{"page": 30}, {"page": 31}]}
    warn = jobcheck.Failure("warning_dropped", '"do not run…" between steps 1 and 2 on page 36')
    fails = {"early": [warn], "late": [jobcheck.Failure("wrong_order", "switched on at step 2 while unplugged")]}
    assert af.repair_pages(early, fails["early"], texts)[:2] == [36, 1]  # the warning's own page is never cut
    assert af.repair_groups([early, late], fails, texts) == [[early], [late]]  # one call each, nothing cut


async def test_an_off_step_clause_is_asked_again_once_with_its_steps_page() -> None:
    job = {"id": "rinse_filter", "title_en": "Rinse the filter", "steps": [
        {"id": "rinse", "text_en": "Rinse the filter.", "quote": "Rinse the filter under running water.", "page": 2,
         "parts": []}]}
    texts = ["An invented page one.", "Rinse the filter under running water until the mesh is clear."]
    asked: list[str] = []

    class Fake:
        async def respond(self, role: str, text: str, **kw: Any) -> Any:
            asked.append(text)
            clause = "The drum paddles are clean." if len(asked) == 1 else "The filter mesh is clear."
            answer = {"steps": [{"key": "rinse_filter/0", "step_id": "rinse",
                                 "done_when": [{"id": "d1", "text_en": clause}]}]}
            return type("R", (), {"usd": 0.0, "parsed": lambda self: answer})()

    await af._clauses({"jobs": [job]}, lane="l3", client=Fake(), who={}, texts=texts)  # type: ignore[arg-type]
    assert len(asked) == 2  # one retry, never more
    assert "--- page 2 ---" in asked[1] and texts[1] in asked[1] and texts[0] not in asked[1]
    assert job["steps"][0]["done_when"][0]["text_en"] == "The filter mesh is clear." and "_off_step" not in job


_INSTALL = ("4 Installation\nPlease read the safety chapter first!\nPlease contact the nearest authorised service for "
            "the installation of the appliance.\n4.1 Location\nPlace the product on a hard and level floor.\n"
            "4.2 Removing the transport bolts\n1. Loosen all transport bolts with a spanner until they turn freely.\n"
            "2. Fit the plastic covers into the holes on the rear panel.\n")
_CARE = ("7 Cleaning and care\nClean the machine regularly, as below.\n7.1 Cleaning the pump filter\n"
         "1. Unscrew the pump filter and take it out of the machine.\n2. Check that the pump wheel turns freely. If "
         "it does not turn, contact the customer service.\n4. Every week, run an empty hot wash.\n")
_FAULTS = ("Faults, what to do\nDisplay and lights do not work during use.\n- Power cut?\n- Fuses tripped? Switch the "
           "fuses on or replace them.\n- If the fault happens more often, call the customer service.\nOther fault "
           "codes.\nSwitch the appliance off, wait 5 seconds and switch it on again. If the code appears again, "
           "call the customer service.\n")


def _pro_job(kind: str, *quotes: tuple[int, str]) -> dict[str, Any]:
    return {"kind": kind, "steps": [{"page": p, "quote": q} for p, q in quotes]}


def test_a_job_in_a_chapter_the_manual_hands_to_the_service_is_a_professionals() -> None:
    pages = [_INSTALL, _CARE]
    bolts = _pro_job("fix", (1, "Loosen all transport bolts with a spanner until they turn freely."),
                     (1, "Fit the plastic covers into the holes on the rear panel."))
    assert af.professional_only(bolts, pages) == "the manual hands the chapter of its steps to the service (page 1)"
    # a conditional handover inside a procedure, and a numbered list item, reserve nothing: the pump job stays ours
    pump = _pro_job("upkeep", (2, "Unscrew the pump filter and take it out of the machine."),
                    (2, "Check that the pump wheel turns freely. If it does not turn, contact the customer service."))
    assert af.professional_only(pump, pages) is None
    # one step quoted from a reserved chapter does not make the job a professional's: most of its steps must be
    mixed = _pro_job("upkeep", (1, "Place the product on a hard and level floor."),
                     (2, "Unscrew the pump filter and take it out of the machine."),
                     (2, "Every week, run an empty hot wash."))
    assert af.professional_only(mixed, pages) is None


def test_a_fault_job_whose_only_action_is_at_the_house_fuses_is_the_services() -> None:
    pages = [_FAULTS]
    fuse = _pro_job("fix", (1, "Fuses tripped? Switch the fuses on or replace them."))
    why = "its only household action is at the house's fuses, and the manual hands the fault to the service"
    assert af.professional_only(fuse, pages) == why  # the handover is the manual's next line, not the job's
    assert af.professional_only(_pro_job("fix", (1, "Fuses tripped? Switch the fuses on or replace them."),
                                         (1, "If the fault happens more often, call the customer service.")),
                                pages) == why
    # a reset on the appliance itself is the household's, though the manual hands a code that comes back on
    reset = _pro_job("fix", (1, "Switch the appliance off, wait 5 seconds and switch it on again."))
    assert af.professional_only(reset, pages) is None


async def test_a_professionals_job_is_refused_with_its_reason(tmp_path: Path, acme_pdf: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][:1]
    d["jobs"].append({**copy.deepcopy(DRAFT["jobs"][0]), "id": "clean_debris_filter_again",
                      "title_en": "Clean the debris filter again", "title_manual": "Clean the debris filter again"})
    for st in d["jobs"][1]["steps"]:
        st["id"] += "_again"
    real = af.professional_only
    monkeypatch.setattr(af, "professional_only", lambda j, texts: (
        "the manual hands the chapter of its steps to the service (page 4)" if j["id"] == "clean_debris_filter"
        else real(j, texts)))
    res = await _build(tmp_path, acme_pdf, d, None)
    assert res.card is not None, res.error
    assert [j["id"] for j in res.card["jobs"]] == ["clean_debris_filter_again"]
    assert [(r["id"], r["reason"], r["page"]) for r in res.card["refused"]][:1] == [
        ("clean_debris_filter", "needs_professional", 4)]
    assert ("jobs[clean_debris_filter]", "professional: the manual hands the chapter of its steps to the service "
            "(page 4)") in [(r.where, r.reason) for r in res.rejected]


async def test_a_widened_quote_is_translated_whole() -> None:
    page = "Filter reinigen.\nDraai los. Spoel het filter onder de kraan schoon tot het gaas helder is.\n"
    step = {"id": "unscrew", "page": 1, "quote": "Draai los.", "quote_en": "Unscrew it."}
    checker = af._Checker([af.Page(1, "1", page)])
    assert checker.cite("jobs[f].steps[unscrew]", step).ok
    assert step["quote"] == "Draai los. Spoel het filter onder de kraan schoon tot het gaas helder is."
    assert step["quote_en"] == ""  # the draft's English covered the short quote only
    kept = {"id": "rinse", "page": 1, "quote": "Spoel het filter onder de kraan schoon.", "quote_en": "Rinse it clean."}
    draft = {"manual_lang": "nl", "jobs": [{"id": "f", "steps": [step, kept]}]}
    asked: list[str] = []

    class Fake:
        async def respond(self, role: str, text: str, **kw: Any) -> Any:
            asked.append(text)
            answer = {"lines": [{"key": "q0", "en": "Unscrew it. Rinse the filter under the tap until the mesh is "
                                 "clear."}]}
            return type("R", (), {"usd": 0.01, "parsed": lambda self: answer})()

    assert await af._translate(draft, {}, lane="l3", client=Fake(), who={}) == 0.01  # type: ignore[arg-type]
    assert len(asked) == 1 and step["quote"] in asked[0] and kept["quote"] not in asked[0]
    assert step["quote_en"] == "Unscrew it. Rinse the filter under the tap until the mesh is clear."
    assert kept["quote_en"] == "Rinse it clean."  # a quote with its own English is not asked again
    # an English manual needs none, and a failed quote is not shown, so it is not translated
    english = {**draft, "manual_lang": "en"}
    assert await af._translate(english, {}, lane="l3", client=Fake(), who={}) == 0.0  # type: ignore[arg-type]
    step["quote_en"] = ""
    bad = {"jobs[f].steps[unscrew]": af.CitationResult(False, 0.0, "low_score")}
    assert await af._translate(draft, bad, lane="l3", client=Fake(), who={}) == 0.0  # type: ignore[arg-type]
    assert len(asked) == 1


async def test_a_failing_quote_on_a_step_the_repair_added_gets_the_quote_repair_once(acme_pdf: Path) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["steps"].insert(0, copy.deepcopy(UNPLUG))
    warning = d["jobs"][0]["steps"].pop(2)
    verbatim = warning["quote"]
    stitched = {**warning, "quote": "Do not remove the filter while the drum is still full of water."}
    asked: list[str] = []

    class Fake:
        config = type("C", (), {"role": staticmethod(lambda name: type("R", (), {"model": WONDER})())})()

        async def respond(self, role: str, text: Any, **kw: Any) -> Any:
            name = kw["format"]["name"]
            asked.append(name)
            if name == "ApplianceFile":
                answer: Any = d
            elif name == "JobRepair":
                answer = {"jobs": [{"key": "clean_debris_filter", "fixable": True, "remove": [],
                                    "add": [{"before": 3, "step": stitched}]}]}
            elif name == "QuoteRepair":
                keys = re.findall(r"jobs\[[^\]]+\]\.steps\[[^\]]+\]", text)
                answer = {"fixes": [{"key": k, "page": 4, "quote": verbatim if "clean_debris_filter" in k else None}
                                    for k in keys]}
            elif name == "StepClauses":
                answer = {"steps": []}
            else:
                answer = {"jobs": [], "lines": []}
            return type("R", (), {"usd": 0.0, "model": WONDER, "text": json.dumps(answer),
                                  "parsed": lambda self: answer})()

    res = await af.build(acme_pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme",
                         country="xx", client=Fake())  # type: ignore[arg-type]
    assert res.card is not None, (res.error, res.rejected)
    job = next(j for j in res.card["jobs"] if j["id"] == "clean_debris_filter")
    assert verbatim in [st["quote"]["text"] for st in job["steps"] if st.get("quote")]
    assert asked.count("QuoteRepair") == 2  # the first pass's planted bad quote, then the added step's, once


def test_a_step_that_points_to_a_page_whose_job_was_dropped_takes_its_job_with_it() -> None:
    pages = [af.Page(1, "40", "Storingen\nWater pressure too low. Clean the inlet filter. ~ Blz. 38\nOpen the tap "
                     "fully, as far as it goes.\n"),
             af.Page(2, "38", "Inlet filter\nUnscrew the inlet hose from the tap and pull the filter out.\n")]
    draft = {"jobs": [
        {"id": "low_pressure", "steps": [{"id": "clean", "page": 1, "quote": "Water pressure too low. Clean the "
                                          "inlet filter."}, {"id": "tap", "page": 1, "quote": "Open the tap fully, "
                                                             "as far as it goes."}]},
        {"id": "inlet_filter", "steps": [{"id": "unscrew", "page": 2, "quote": "Unscrew the inlet hose from the "
                                          "tap and pull the filter out."}]}]}
    card = {"jobs": [{"id": "low_pressure"}]}  # the filter job was dropped
    assert af.points_to_dropped(draft, card, pages) == {"low_pressure": ("clean", 1, 2)}  # printed 38 is PDF page 2
    assert af.points_to_dropped(draft, {"jobs": [{"id": "low_pressure"}, {"id": "inlet_filter"}]}, pages) == {}
    # a step with no pointer on its own line is not taken for the line below it
    draft["jobs"][0]["steps"] = draft["jobs"][0]["steps"][1:]
    assert af.points_to_dropped(draft, card, pages) == {}


async def test_a_job_pointing_at_nothing_is_refused_and_named(tmp_path: Path, acme_pdf: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][:1]
    d["jobs"].append({**copy.deepcopy(DRAFT["jobs"][0]), "id": "clean_debris_filter_again",
                      "title_en": "Clean the debris filter again", "title_manual": "Clean the debris filter again"})
    for st in d["jobs"][1]["steps"]:
        st["id"] += "_again"
    monkeypatch.setattr(af, "points_to_dropped", lambda draft, card, pages: (
        {"clean_debris_filter": ("open_flap", 4, 5)} if "clean_debris_filter" in [j["id"] for j in card["jobs"]]
        else {}))
    res = await _build(tmp_path, acme_pdf, d, None)
    assert res.card is not None, res.error
    assert [j["id"] for j in res.card["jobs"]] == ["clean_debris_filter_again"]
    assert [(r["id"], r["reason"], r["page"]) for r in res.card["refused"]][:1] == [("clean_debris_filter", "other", 4)]
    assert ("jobs[clean_debris_filter].steps[open_flap]", "points_to_dropped_job: its quote sends to page 5, whose "
            "job was not built") in [(r.where, r.reason) for r in res.rejected]


def test_the_job_a_pointer_names_is_found_by_its_title_on_a_page_with_another_job() -> None:
    pages = [af.Page(1, "40", "Faults\nWater pressure too low. Clean the filter. ~  Blz.  38\n"),
             af.Page(2, "38", "Siphon\nPull the drain hose off the siphon and rinse it.\nFilter in the inlet\nUnscrew "
                     "the inlet hose from the tap and pull the filter out.\n")]
    draft = {"jobs": [
        {"id": "low_pressure", "title_manual": "Low pressure", "steps": [
            {"id": "clean", "page": 1, "quote": "Water pressure too low. Clean the filter."}]},
        {"id": "siphon", "title_manual": "Clean the siphon hose", "steps": [
            {"id": "rinse", "page": 2, "quote": "Pull the drain hose off the siphon and rinse it."}]},
        {"id": "inlet_filter", "title_manual": "Clean the inlet filter", "steps": [
            {"id": "unscrew", "page": 2, "quote": "Unscrew the inlet hose from the tap and pull the filter out."}]}]}
    card = {"jobs": [{"id": "low_pressure"}, {"id": "siphon"}]}  # the siphon job on the same page stays
    assert af.points_to_dropped(draft, card, pages) == {"low_pressure": ("clean", 1, 2)}
    assert af.points_to_dropped(draft, {"jobs": [*card["jobs"], {"id": "inlet_filter"}]}, pages) == {}


# An invented page in a manual's layout: steps after a symbol, a warning box (its title and sentences) after one.
_BOX_PAGE = ("Cleaning the filters\n"
             "■ Turn the lower spray arm clockwise until\nthe arrow points at the lock.\n"
             "■ Risk of damage from dirt in the pump.\n"
             "The lower spray arm holds the filters in place. Without the fil-\nters, food can block the pump.\n"
             "■ Put the basket back in the machine.\n"
             "14\n")
_BOX = ("Risk of damage from dirt in the pump. The lower spray arm holds the filters in place. "
        "Without the filters, food can block the pump.")


def test_a_whole_warning_box_of_three_sentences_passes():
    checker = af._Checker([af.Page(1, "14", _BOX_PAGE)])
    assert af.sentences(_BOX) == 3 and checker.check("w", 1, _BOX).reason != "too_long"


def test_a_step_quote_of_three_sentences_still_fails():
    page = "■ Turn the arm. Lift it out. Rinse it under the tap.\n■ Put the arm back.\n"
    quote = "Turn the arm. Lift it out. Rinse it under the tap."
    assert not af.warning_box(page, quote)
    assert af._Checker([af.Page(1, "1", page)]).check("s", 1, quote).reason == "too_long"


def test_a_warning_box_of_four_sentences_fails():
    page = ("■ Risk of damage from dirt in the pump. The lower spray arm holds the filters in place.\n"
            "Without the filters, food can block the pump. Never run it without them.\n■ Put the basket back.\n")
    quote = _BOX + " Never run it without them."
    assert af.box_sentences(page, quote) == 4  # no title line of its own: every sentence counts
    assert af._Checker([af.Page(1, "14", page)]).check("w", 1, quote).reason == "too_long"


def test_part_of_a_warning_box_is_not_a_whole_box():
    head = "Risk of damage from dirt in the pump. The lower spray arm holds the filters in place."
    tail = ("The lower spray arm holds the filters in place. Without the filters, food can block the pump. "
            "Put the basket back in the machine.")
    assert af.warning_box(_BOX_PAGE, _BOX)
    assert not af.warning_box(_BOX_PAGE, head)  # stops before the box does
    assert not af.warning_box(_BOX_PAGE, tail)  # opens mid-box and runs into the next step


def test_a_warning_box_joined_across_two_pages_fails():
    one = "■ Risk of damage from dirt in the pump.\nThe lower spray arm holds the filters in place.\n14\n"
    two = "Without the filters, food can block the pump.\n■ Put the basket back in the machine.\n15\n"
    checker = af._Checker([af.Page(1, "14", one), af.Page(2, "15", two)])
    assert checker.check("w", 1, _BOX).reason == "too_long"
    assert checker.check("w", 2, _BOX).reason == "too_long"


# The same box with its own title printed on its own line, as Miele prints its boxes.
_TITLED_PAGE = ("■ Turn the lower spray arm clockwise.\n"
                "■ Risk of damage from dirt\nin the pump.\n"
                "The lower spray arm holds the filters. Without the filters, food can block the pump.\n"
                "Never rinse without the filters.\n"
                "■ Put the basket back in the machine.\n"
                "14\n")
_TITLED = ("Risk of damage from dirt in the pump. The lower spray arm holds the filters. Without the filters, food "
           "can block the pump. Never rinse without the filters.")


def test_a_warning_box_with_its_title_and_three_sentences_passes():
    assert af.sentences(_TITLED) == 4 and af.box_sentences(_TITLED_PAGE, _TITLED) == 3
    assert af._Checker([af.Page(1, "14", _TITLED_PAGE)]).check("w", 1, _TITLED).reason != "too_long"


def test_a_warning_box_with_its_title_and_four_sentences_fails():
    page = _TITLED_PAGE.replace("without the filters.", "without the filters. Clean them every week.")
    quote = _TITLED + " Clean them every week."
    assert af.box_sentences(page, quote) == 4
    assert af._Checker([af.Page(1, "14", page)]).check("w", 1, quote).reason == "too_long"


def test_a_box_must_quote_its_title():
    body = _TITLED.split(". ", 1)[1]  # the three sentences without the title line: not the whole box
    assert not af.warning_box(_TITLED_PAGE, body) and not af.within_cap(_TITLED_PAGE, body)


def test_a_title_like_line_inside_the_box_counts():
    page = ("■ Risk of damage from dirt in the pump. The arm holds the filters.\n"
            "Caution: filters.\n"  # short and on its own line, but not where the box starts
            "Without them food can block the pump. Never rinse without them.\n"
            "■ Put the basket back.\n")
    quote = ("Risk of damage from dirt in the pump. The arm holds the filters. Caution: filters. Without them food "
             "can block the pump. Never rinse without them.")
    assert af.box_sentences(page, quote) == 5 and not af.within_cap(page, quote)
    starts_mid_line = ("Risk of damage from dirt in the pump. The arm holds the filters. Without them food can block "
                       "the pump. Never rinse without them.")
    page2 = ("■ Risk of damage from dirt in the pump. The arm holds the filters.\n"
             "Without them food can block the pump. Never rinse without them.\n■ Put the basket back.\n")
    assert af.box_sentences(page2, starts_mid_line) == 4  # its first sentence shares a line: no title, all count


def test_a_passing_step_quote_inside_a_warning_box_is_widened_to_the_whole_box():
    checker = af._Checker([af.Page(1, "14", _TITLED_PAGE)])
    o = {"page": 1, "quote": "Without the filters, food can block the pump.", "quote_en": "Short."}
    assert checker.cite("jobs[j].steps[s]", o).ok
    assert o["quote"] == _TITLED and o["quote_en"] == ""  # its English is redone for the whole box


def test_a_step_quote_outside_a_warning_box_is_left_alone():
    checker = af._Checker([af.Page(1, "14", _TITLED_PAGE)])
    o = {"page": 1, "quote": "Turn the lower spray arm clockwise."}
    assert checker.cite("jobs[j].steps[s]", o).ok and o["quote"] == "Turn the lower spray arm clockwise."


def test_a_quote_inside_a_box_that_fails_the_rule_is_not_widened():
    page = _TITLED_PAGE.replace("without the filters.", "without the filters. Clean them every week.")
    o = {"page": 1, "quote": "Without the filters, food can block the pump."}
    assert af.whole_box(page, o["quote"]) is None  # title + 4: over the cap
    assert af._Checker([af.Page(1, "14", page)]).cite("jobs[j].steps[s]", o).ok
    assert o["quote"] == "Without the filters, food can block the pump."


_FILTERS_PAGE = ("6 Cleaning the filters\n■ Switch the machine off at the on/off button.\n"
                 "■ Lift the filter combination out of the cabinet.\n■ Rinse all the parts under running water.\n"
                 "■ Put the filter combination back flat on the floor.\nPage 7\n")


def _filters_job() -> dict[str, Any]:
    def step(sid: str, text: str, quote: str) -> dict[str, Any]:
        return {"id": sid, "text_en": text, "text_manual": text, "page": 7, "quote": quote, "done_when": [],
                "parts": [], "power": "any", "zones": [], "view": "front", "look_only_lines": []}
    return {"id": "clean_filters", "title_en": "Clean the filters", "title_manual": "Clean the filters",
            "kind": "upkeep", "interval": None, "fault_codes": [], "landlord": False, "duration": None,
            "steps": [step("off", "Switch the machine off.", "Switch the machine off at the on/off button."),
                      step("lift", "Lift the filters out.", "Lift the filter combination out of the cabinet."),
                      step("back", "Put the filters back.", "Put the filter combination back flat on the floor.")]}


async def _skipped_build(acme_pdf: Path, monkeypatch: pytest.MonkeyPatch, fix: dict[str, Any]) -> Any:
    pages = [*extract(acme_pdf), af.Page(7, "7", _FILTERS_PAGE)]
    monkeypatch.setattr(af, "extract", lambda *a, **k: pages)
    d = copy.deepcopy(DRAFT)
    d["jobs"].append(_filters_job())
    asked: dict[str, str] = {}

    class Fake:
        config = type("C", (), {"role": staticmethod(lambda name: type("R", (), {"model": WONDER})())})()

        async def respond(self, role: str, text: Any, **kw: Any) -> Any:
            name = kw["format"]["name"]
            asked[name] = text if isinstance(text, str) else json.dumps(text)
            answer: Any = {"jobs": [], "lines": []}
            if name == "ApplianceFile":
                answer = d
            elif name == "JobRepair":
                answer = {"jobs": [{"key": "clean_filters", **fix}]}
            elif name == "QuoteRepair":
                answer = {"fixes": []}
            elif name == "StepClauses":
                answer = {"steps": []}
            return type("R", (), {"usd": 0.0, "model": WONDER, "text": json.dumps(answer),
                                  "parsed": lambda self: answer})()

    res = await af.build(acme_pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme",
                         country="xx", client=Fake())  # type: ignore[arg-type]
    return res, asked


async def test_a_skipped_printed_step_goes_to_the_job_repair_which_adds_it_verbatim(
        acme_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rinse = {**_filters_job()["steps"][0], "id": "rinse", "text_en": "Rinse the parts.",
             "text_manual": "Rinse all the parts under running water.",
             "quote": "Rinse all the parts under running water."}
    res, asked = await _skipped_build(acme_pdf, monkeypatch, {"fixable": True, "remove": [],
                                                              "add": [{"before": 3, "step": rinse}]})
    assert "step_skipped" in asked["JobRepair"] and "rinse all the parts" in asked["JobRepair"]
    assert res.card is not None, (res.error, res.rejected)
    job = next(j for j in res.card["jobs"] if j["id"] == "clean_filters")
    assert [st["id"] for st in job["steps"]] == ["off", "lift", "rinse", "back"]


async def test_a_skipped_printed_step_the_repair_cannot_add_drops_the_job_and_names_it(
        acme_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res, _ = await _skipped_build(acme_pdf, monkeypatch, {"fixable": False, "remove": [], "add": []})
    assert res.card is not None and "clean_filters" not in [j["id"] for j in res.card["jobs"]]
    assert any(r.where == "jobs[clean_filters]" and "step_skipped" in r.reason for r in res.rejected)
