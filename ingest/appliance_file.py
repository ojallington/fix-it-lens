"""The appliance file: one call per manual builds a card, and code checks every quote before the card is kept.

    result = await build(pdf, brand="bosch", model="SMV4HAX40N", source_url=..., lane="l3")
    result.card      # a dict valid against contracts/card.schema.json, or None (a failed ingest)
    result.rejected  # every quote that failed the citation check, and what was dropped for it
    result.rail      # "muse-wonder: manual → 41 steps · 6 red zones · 3 intervals · every one cited"

The model is the `appliance_file` role in config/models.toml (muse-wonder-1.0). It builds text only: steps, parts to
locate, red zones with pages, upkeep intervals, each with a verbatim quote and a page. Code then:
1. checks every quote against the PDF text on its page (`citation.verify`), and moves a quote found on another page;
2. sends the failures back once, with the text of the cited page and its neighbours, for the verbatim sentence;
3. drops whatever still fails, and lists it: a job with an uncited step, a red zone, an interval or a duration;
4. assembles the card and validates it against the schema.
The rules pass is not here: the card loader in app/ runs the rules over each card.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import jsonschema
import yaml
from pypdf import PdfReader, PdfWriter

import calls
from ingest import faultcodes, jobcheck, models, symbols, titles
from ingest.cards import CARD_SCHEMA
from ingest.citation import CitationResult, locate, norm, verify, widen
from ingest.pages import Page, extract, file_sha256, page_hash
from ingest.progress import DRAFT_FROM, DRAFT_TO, JobStream, Tracker, page_lang

MAX_SENTENCES = 2
WARNING_BOX_SENTENCES = 3  # a whole warning box, verbatim on one page (warning_box)
_HAS_WITNESS = "witness" in CARD_SCHEMA["$defs"]["Step"]["properties"]
Source = Literal["maker_site", "household_upload"]  # contracts 1.23 manual.source
Who = dict[str, Any]  # respond() kwargs naming whose call it is: household, household_cap_usd
KINDS = ["dishwasher", "washer", "dryer", "boiler", "fridge", "oven", "hob", "hood", "other"]
# The rules' zone vocabulary. Cards write nl.<family>.<zone>; every country file carries the same zones.
ZONE_IDS = ["gas.boiler", "gas.casing", "gas.flue", "gas.tap", "heat.hot_water", "mains.casing",
            "pressure.relief_valve", "water.aquastop"]
# An older zone vocabulary, mapped onto the rules' one (None: not a red zone; raised at run time or a
# user-section job with a caution). Kept so a draft made with the old list still assembles.
ZONE_REMAP: dict[str, str | None] = {
    "mains.back_panel": "mains.casing", "mains.control_board": "mains.casing",
    "mains.mains_cable": None, "mains.plug_socket": None,
    "heat.heating_element": "heat.hot_water", "heat.hot_surface": "heat.hot_water",
    "water.inlet_valve": "water.aquastop",
    "pressure.pressure_valve": "pressure.relief_valve", "pressure.expansion_vessel": "pressure.relief_valve",
    "gas.gas_tap": "gas.tap", "gas.gas_valve": "gas.tap", "gas.burner": "gas.tap", "co.flue": "gas.flue",
    "mechanical.pump_impeller": None, "mechanical.glass_shards": None,
    "chemical.detergent": None, "chemical.descaler": None,
}
# The rules' sealed-boiler allow-list: a boiler job id is one of these or starts with one plus "_"; anything else is
# a STOP at load, so ingest drops it and lists it.
BOILER_JOBS = ["read_pressure", "top_up", "bleed_radiators", "reset_per_manual", "power_cycle"]
# The draft names each job's task from this list; on a boiler, code gives the job its id from the task, so a job is
# never lost to a model-chosen id. "other" on a boiler is dropped, and listed with its title.
TASKS = [*BOILER_JOBS, "other"]
# The only look-only lines G0 exempts, byte-exact (their sha256s are in rules/nl.yaml).
GAS_TAP_LOOK_ONLY = ["The yellow tap is gas. Check it's open: just look.",
                     "De gele kraan is gas. Kijk of hij open staat: alleen kijken."]


def zone_rule(rule: str) -> str | None:
    return rule if rule in ZONE_IDS else ZONE_REMAP.get(rule)


def boiler_job_ok(job_id: str) -> bool:
    return any(job_id == a or job_id.startswith(a + "_") for a in BOILER_JOBS)


LANGS = ["nl", "en", "de", "fr", "it", "es", "pl", "sv", "da", "fi", "pt"]


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


_STR: dict[str, Any] = {"type": "string", "maxLength": 300}
_SHORT: dict[str, Any] = {"type": "string", "maxLength": 80}
_INT: dict[str, Any] = {"type": "integer"}
_CITE = {"page": _INT, "quote": {"type": "string", "maxLength": 400}}


# why a maintenance procedure in the manual is not a job ("" when it is one, named by job_id)
DROP_REASONS = ["", "technician_only", "installation_or_moving", "operating_not_upkeep", "same_as_another_job"]


def _arr(items: dict[str, Any], n: int) -> dict[str, Any]:
    return {"type": "array", "maxItems": n, "items": items}

APPLIANCE_FILE_SCHEMA: dict[str, Any] = _obj({
    "appliance_kind": {"type": "string", "enum": KINDS},
    "manual_lang": {"type": "string", "enum": LANGS},
    "title": _STR,
    "doc_id": _STR,
    "parts": _arr(_obj({
        "id": _SHORT, "name_manual": _SHORT, "name_en": _SHORT,
        "locate": {"type": "string", "enum": ["function", "appearance"]},
        "cue": {"type": "string", "maxLength": 200}, "sam_text": {"type": "string", "maxLength": 40},
        "kind": {"type": "string", "enum": ["allowed", "red_zone"]},
    }), 60),
    "red_zones": _arr(_obj({
        "id": _SHORT, "part_id": _SHORT, "rule": {"type": "string", "enum": ZONE_IDS}, **_CITE,
    }), 20),
    "jobs": _arr(_obj({
        "id": _SHORT, "title_en": _SHORT, "title_manual": _SHORT, "kind": {"type": "string", "enum": ["upkeep", "fix"]},
        "task": {"type": "string", "enum": TASKS},
        "due_name_en": _SHORT, "due_name_manual": _SHORT,
        "after": _arr(_obj({"job_id": _SHORT, "because_en": _STR, **_CITE}), 3),
        "interval": {"anyOf": [{"type": "null"}, _obj({
            "every": _INT, "unit": {"type": "string", "enum": ["day", "week", "month", "year", "cycle"]}, **_CITE,
        })]},
        "fault_codes": _arr(_SHORT, 12),
        "landlord": {"type": "boolean"},
        "duration": {"anyOf": [{"type": "null"}, _obj({"minutes": {"type": "number"}, **_CITE})]},
        "steps": _arr(_obj({
            "id": _SHORT, "text_en": _STR, "text_manual": _STR, **_CITE, "quote_en": _STR,
            "done_when": _arr(_obj({"id": _SHORT, "text_en": _STR}), 3),
            "parts": _arr(_SHORT, 6),
            "power": {"type": "string", "enum": ["on", "off", "any"]},
            "zones": _arr(_SHORT, 4),
            "view": {"type": "string", "enum": ["front", "side"]},
            "look_only_lines": {"type": "array", "items": _STR},
            "witness": {"anyOf": [{"type": "null"}, _obj({
                "part": _SHORT, "expect": {"type": "string", "enum": ["present", "absent"]},
                "anchor": {"anyOf": [{"type": "null"}, _SHORT]},
            })]},
        }), 20),
    }), 30),
    "keywords": _arr(_SHORT, 40),
    "troubleshooting": _arr(_obj({
        "id": _SHORT, "codes": _arr(_SHORT, 12),
        **{f"{f}_{w}": _STR for f in ("indicator", "symptom", "meaning", "cause", "remedy") for w in ("en", "manual")},
        "remedy_kind": {"type": "string", "enum": ["user", "technician", "installer"]},
        "escalate_to": {"type": "string", "enum": ["technician", "installer", "none"]},
        "job_id": _SHORT, "prevented_by": _arr(_obj({"job_id": _SHORT, **_CITE}), 3),
        "repeat": {"anyOf": [{"type": "null"}, _obj({"count": _INT, "days": _INT, **_CITE})]},
        **_CITE, "quote_en": _STR,
    }), 80),
    "models_listed": _arr(_obj({"code": _SHORT, **_CITE}), 40),
    "upkeep_procedures": _arr(_obj({
        "heading": _SHORT, "page": _INT, "job_id": _SHORT,
        "drop_reason": {"type": "string", "enum": DROP_REASONS},
    }), 40),
    "checklist": _arr(_obj({"item": _SHORT, "in_manual": {"type": "boolean"}, "heading": _SHORT, "page": _INT,
                            "job_id": _SHORT}), 120),
})

_INSTRUCTIONS = """\
You build the appliance file for one home appliance from its manufacturer's manual (the attached PDF). The file is an
index in front of the manual, used by an app that guides a person through upkeep and simple fixes with a phone camera.

Quotes and pages, the rule that matters most:
- Every `quote` is copied VERBATIM from the manual, character for character, in the manual's own language. Never
  translate a quote, never paraphrase, never join text from two places. One or two whole sentences, at least 20
  characters. Keep every number, direction (left/right, clockwise), negation and "first/before/after" exactly.
  A step sentence shorter than 20 characters ("De zeven reinigen.") is quoted together with the next sentence as
  printed. In a fault table, where a cell is shorter than 20 characters, quote the cause and the action together.
- `page` is the PDF page index: 1 is the first page of the PDF file, whatever number is printed on it.
- Code checks each quote against the PDF text on that page and drops anything that does not match.

What to extract:
- jobs: every upkeep task the user may do (cleaning filters, descaling, regeneration salt, rinse aid, door seals,
  pressure top-ups, bleeding radiators) and every user fix the manual gives for a fault code or symptom. `kind` is
  `upkeep` or `fix`. Only what the manual tells the user to do; never a step the manual reserves for a technician.
  Every line of a fault, indicator or symptom table whose remedy the user carries out gets its own fix job, even
  when the remedy is one or two steps ("Close the door properly.", "Wait until the drum has cooled."), with the
  row's `job_id` naming it; a line whose only remedy is to call the service or the installer gets none.
  Each task is one job, listed once: never repeat a job or split one task into several jobs.
  `title_en` and `title_manual` are sentence case and start with a verb ("Clean the filters", "Refill the rinse
  aid"); a fix job names its fault code in brackets at the end ("Clean the inlet filter (fault E:32-00)"). A title
  is the task alone: never the model code, the maker's name or a note in brackets.
- Whole procedures. A job is the manual's procedure, all of it, never only its cleaning step:
  - The first job is the appliance's main hands-on upkeep: on a washing machine cleaning the drain pump or pump
    filter (with draining the water left in it), on a dishwasher cleaning the filter system, on a fridge or freezer
    defrosting and cleaning, on a boiler topping up the water pressure. Then the other upkeep, then the fixes.
  - Always make a job of each of these when the manual gives it to the user, however long it is or however many
    warnings it has: cleaning the drain pump or pump filter, draining the remaining water (emergency drain hose),
    cleaning the water inlet filters, cleaning the detergent drawer, the emergency door release, defrosting,
    filling and bleeding the heating system.
  - Every warning, caution or "do not" line the manual prints inside the procedure or right above its first step
    (hot water, let it cool down, the drum must be stopped or empty, keep a bowl ready, a heavy or glass door, do
    not run the appliance without the filter) is a step of its own, at the place it is printed, with its quote;
    `text_en` says what to do or not do ("Wait until the water has cooled down."). Never leave one out.
  - Every closing step is a step: putting back each part the job took out, closing each flap, cover, lid or door it
    opened, screwing a part back in, and the check the manual gives at the end ("make sure the filter is tight").
  - When the manual says to unplug or switch off for the procedure, that is a step where it says so, with power
    off; when it says to plug back in, switch on or open the tap again, that is a step where it says so. Plugging
    back in comes before switching on. When the manual says once, in its safety or care chapter, to unplug or
    switch off before any cleaning or maintenance, every upkeep job that opens, removes or cleans a part starts
    with that step, quoted from where the manual says it.
  - Keep repeats and loops as printed ("repeat until no more water comes out"). Each numbered item in the
    procedure is at least one step; never merge two numbered items into one step.
  - When a step points to another section for how to do it ("see Cleaning the drain pump"), write that section's
    steps in its place.
- interval: only when the manual states how often, with the quote that states it; otherwise null. `unit` is day,
  week, month, year or cycle.
- duration: only when the manual states how long the WHOLE job takes; otherwise null. A wait inside a step ("wait at
  least 2 minutes") is not the job's duration.
- steps: in the manual's order. The app speaks the step text aloud, so `text_en` is one instruction as a person
  would say it, in natural spoken English with its articles and objects, at most 20 words ("Turn the coarse filter
  anticlockwise and lift the filter system out.", not "Turn coarse filter anticlockwise and remove filter system.").
  `text_manual` says the same in the manual's language as a spoken instruction (Dutch: "Draai de grove zeef tegen de
  klok in en neem het zeefsysteem eruit."), spelt correctly and saying no more and no less than the quote.
  `quote_en` is an English translation of the quote, shown under it for readers who do not read the manual's
  language (the quote itself stays verbatim). `done_when` lists 1-3 conditions a camera can see when the step is
  done, each a whole sentence ("The coarse filter is out of the sump.", "The arrow marks on the filter line up.").
  Every step that moves, opens,
  removes, refits, turns, fills or cleans a part has at least one; an empty list only for a step with nothing to see
  (pressing a button, waiting, unplugging). The step check judges these clauses: without them the step cannot pass.
  `power` is `off` when the manual
  says to switch off or unplug first, else `any`. `view` is `side` when the step's hands come within reach of a red
  zone, else `front`. `zones` are red-zone ids near the step.
  `witness`: the one part whose presence a camera checks when the step is done, with `expect` `absent` when the step
  takes it out ("filter lifted out": part filter_system, anchor sump) or `present` when it puts it back ("drain cap
  refitted": part drain_cap, anchor drain). `anchor` is the part it sits on or in, or null. Null for a step with no
  part to see come or go (unplugging, pressing a button, waiting).
- parts: each part a step works on, and each red-zone part. `id` is a lower-case slug. `name_manual` as the manual
  names it; `name_en` in English. One name each, the one a person would say ("micro filter"), never two names
  joined with "/" or "or"; put the other names in keywords. `cue`: how it looks and where it is ("round grey
  cylinder with a twist handle, bottom centre"). `sam_text`: one short noun phrase for a segmenter ("filter",
  "drain pump cover"), no verbs, no "and". `locate`: `appearance` if it is found by how it looks, `function` if by
  what it does. `kind` is `red_zone` only for a part the user must not open or touch.
- red_zones: physical parts of the appliance the manual says the user must not open, remove or touch, or that carry
  mains power, gas, heat or pressure (the casing, a back panel, the heating element, the gas tap, the flue). Not
  general advice (which cleaner to use), not the mains cable or plug (unplugging is a step), not a pump impeller or
  a part the manual lets the user clean with care. `rule`: mains.casing (casing, back panel, control board),
  heat.hot_water (heating element, hot surfaces and water), water.aquastop, pressure.relief_valve, gas.tap (gas tap or
  valve, burner), gas.flue, gas.casing (a boiler's front mantel), gas.boiler (inside a boiler: heat exchanger,
  ignition). Each with the quote that says so.
- task: on a boiler, what the job is: read_pressure (read the pressure gauge), top_up (fill or top up the heating
  water, including the bleeding and pressure check the manual does as part of it), bleed_radiators (bleed radiators
  on their own), reset_per_manual (a reset the manual tells the user to do), power_cycle (switch off and on), or
  other. Write each boiler procedure the manual gives the user as one job with its task, whole, from its first step
  to its last. On any other appliance, task is other.
- look_only_lines: leave empty; code writes the exact lines.
- due_name_en / due_name_manual: the short noun a reminder names ("filter", "salt"); "" if the title says it.
- after: for an upkeep job the manual ties to another ("check the pressure after bleeding the radiators"): the other
  job's id, why in English, and the quote that ties them. Usually none.
- fault_codes: each code a display shows for this job, copied exactly as the manual prints it ("E:32-00", "F10",
  "E:36 / -10" as printed). Only codes: never a symptom, a word or a placeholder ("FXXX"); [] for a symptom.
- keywords: part names in the manual's language and fault codes, to help speech recognition.
- title and doc_id: the manual's title and document number as printed ("" if none).
- troubleshooting: one row per line of the manual's fault, indicator or symptom tables, every line, including those
  whose remedy is to call the service or the installer. `codes`: the display codes of that line, exactly as printed,
  or [] for a light or a symptom. `indicator_*`: what the display or a light shows when it has no code ("the tap
  symbol lights up", "the red light blinks 3 times"), else "". `symptom_*`: what the person notices ("water is left in
  the bottom"), else "". `meaning_*`: what the line says is wrong, in one short sentence. `cause_*`: the cause the
  line gives, else "". `remedy_*`: what the line says to do, in one or two short sentences. `remedy_kind`: user when
  the person does it, technician when the manual says to call the service, a technician or an electrician, installer
  when it says the installer. `escalate_to`: for a user remedy that goes on "if the problem persists, call ...", who;
  else none. `job_id`: the fix job above that carries out a user remedy, else "". `prevented_by`: an upkeep job the
  manual itself says prevents this fault, with the quote that says so; usually none. `repeat`: only when the manual
  says how many times in how many days means calling someone ("a third top-up within a year"), with that quote; else
  null. `quote`: the line itself, verbatim, its code, cause and remedy cells together as printed; `quote_en` its
  English translation. *_en in English, *_manual in the manual's language, from the quote only.
- models_listed: every model or type code the manual prints as one it covers (cover, title page, model table), as
  printed, including the manual's own placeholders ("WMN...", "SMV4HA*"); each with the quote that prints it.
  [] when it names none. Never a code from your own knowledge.
- upkeep_procedures: every procedure in the manual for cleaning, care, maintenance or a user fix, by its heading as
  printed and its page. `job_id` the job above that carries it out; else "" and `drop_reason`: technician_only,
  installation_or_moving (installing, moving, frost protection), operating_not_upkeep (using the appliance), or
  same_as_another_job. A procedure for the user's upkeep with no job is a mistake: make it a job.
"""

CHECKLISTS = Path(__file__).parent / "checklists"  # <kind>.yaml: the jobs a manual of that kind usually gives, by name


def checklists() -> dict[str, list[dict[str, Any]]]:
    """The user items of each appliance kind's checklist (professional ones are not asked about)."""
    out = {}
    for f in sorted(CHECKLISTS.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        out[d["appliance_kind"]] = [i for i in d["items"] if i["who"] == "user"]
    return out


def checklist_instructions(lists: dict[str, list[dict[str, Any]]]) -> str:
    """The reader's checklist: it asks about each job a manual of the type usually gives; it never writes a step."""
    lines = ["""\
- checklist: the jobs a manual of each appliance type usually gives the user, listed below by type. For each item of
  this appliance's type, look for it in this manual. If the manual gives it, `in_manual` is true, `heading` is the
  heading or sentence where it does, copied exactly as printed, `page` its page, and `job_id` the job above that
  carries it out: an item the manual gives is a job, built whole like any other, every step quoted from the
  manual. If the manual does not give it, `in_manual` is false, `heading` "", `page` 0 and `job_id` "": never a
  job for it. The list only asks; nothing in it is a step. Leave this empty for a type that has no list."""]
    for kind, items in lists.items():
        lines.append(f"  {kind}:")
        lines += [f"  - {i['id']}: {i['names']['en']} ({i['kind']}{'; ' + i['note'] if i.get('note') else ''})"
                  for i in items]
    return "\n".join(lines) + "\n"


INSTRUCTIONS = _INSTRUCTIONS + checklist_instructions(checklists())

REPAIR_INSTRUCTIONS = """\
Some quotes in an appliance file did not match the manual's text on the cited page. For each item you get the quote
and the extracted text of the cited page and its neighbours. Return the sentence from that text that says the same
thing, copied VERBATIM (one or two whole sentences, in the text's own language, every number, direction and negation
kept), with the page it is on. A quote must be at least 20 characters: when the sentence is shorter, return it
together with the sentence that follows it, as printed. If no sentence in the text says it, return null for the
quote. Never write a quote that is not in the text.
"""

REPAIR_SCHEMA: dict[str, Any] = _obj({"fixes": {"type": "array", "items": _obj({
    "key": _STR, "page": _INT, "quote": {"anyOf": [{"type": "null"}, _STR]},
})}})


JOB_REPAIR_INSTRUCTIONS = """\
Each job below failed a check of the job as a whole, named in its `failures`. For each job, say how to fix it from
the manual's text you are given, or that it cannot be fixed from that text:
- leaves_state "mains off" / "power off" / "water_supply off": add the step that undoes it (plug the appliance back
  in, switch it back on, open the water tap again) at the end, quoted from where the manual says it for the same
  procedure or, for a fix job, the same fault code. Never a sentence from another procedure: when the job's own
  procedure and code never say it, set fixable false.
- leaves_state "<part> removed or opened ..." / "<part> opened ...; never closed": add the manual's step that
  refits or closes that part, where the procedure says it.
- refers_outside: remove that step and add, in its place, the manual's own steps it points to, or set fixable false.
- precondition_missing "<word>" above step N: add, before step N, a step that carries that block: the condition to
  check or the warning to heed, quoted from the block itself.
- warning_dropped "<words>..." between steps N and N+1 (or above step N) on page P: add, at that place, a step that
  carries that warning, quoted from the warning's own sentence on page P; `text_en` says what to do or not do.
- step_skipped "<words>..." between steps N and N+1 on page P: the manual prints a step there that the job leaves
  out; add it at that place (and any other printed step the job skips there), each quoted VERBATIM from its own
  line on page P, or set fixable false.
- wrong_order "switched on at step N while unplugged": add the manual's step that plugs the appliance back in before
  step N, quoted from the same procedure; when the procedure never says it, set fixable false.
`remove` lists step numbers (n) to drop; each `add` goes before step number `before` (the number of steps to add at
the end). Every added step follows the appliance file's rules: `quote` VERBATIM from the given page text, in its own
language, one or two whole sentences, at least 20 characters, every number, direction and negation kept; `page` is
the page number given with that text; `text_en` one spoken instruction; `text_manual` the same in the manual's
language. Never write a step or a quote that is not in the text. `fixable` false when the text does not say it.
"""

CLAUSE_INSTRUCTIONS = """\
These are the steps of an appliance job, each with its instruction and the manual's sentence it comes from. A camera
and a judge will check, at the end of each step, that it was done. For each step give:
- done_when: 1-3 short conditions a camera can see once the step is done, about the parts the step works on, each a
  whole sentence about that step's own result ("The coarse filter is out of the sump.", "The arrow marks on the
  filter line up.", "The spray arm is back on its mount and turns freely."). A step that checks or looks at a part
  gets a condition about seeing it, not about putting it back. Only what the instruction and its sentence say; no
  condition the sentence does not support.
  An empty list only when the step leaves nothing to see (pressing a button, waiting, unplugging).
- step_id: a lower-case verb_object slug for the step ("remove_filter_system", "refit_lower_spray_arm").
Return every step, keyed by its key.
"""

JOB_REPAIR_SCHEMA: dict[str, Any] = _obj({"jobs": {"type": "array", "items": _obj({
    "key": _SHORT, "fixable": {"type": "boolean"}, "remove": _arr(_INT, 20),
    "add": _arr(_obj({"before": _INT, "step": APPLIANCE_FILE_SCHEMA["properties"]["jobs"]["items"]["properties"]
                      ["steps"]["items"]}), 6),
})}})
JOB_REPAIR_FORMAT = {"type": "json_schema", "name": "JobRepair", "schema": JOB_REPAIR_SCHEMA, "strict": True}

TITLE_INSTRUCTIONS = """\
Each of these appliance jobs has a title that is not a plain task. Give each a new title in English and in the
manual's language: sentence case, starting with a verb, the task alone ("Clean the drain pump", "Defrost the
freezer", "Open the door with the emergency release"). Never the model code, the maker's name, a note in brackets
or two names joined by a slash. A fix job ends with its fault code in brackets, as given ("(fault E20)"). Use only
what the job's steps say it does. Return every job, keyed by its key.
"""
TITLE_SCHEMA: dict[str, Any] = _obj({"jobs": _arr(_obj({"key": _SHORT, "title_en": _SHORT, "title_manual": _SHORT}),
                                                  60)})
TITLE_FORMAT = {"type": "json_schema", "name": "JobTitles", "schema": TITLE_SCHEMA, "strict": True}

TRANSLATE_INSTRUCTIONS = """\
Each of these is a line quoted from an appliance manual. Translate each whole line into plain English, sentence for
sentence: every sentence of the line, nothing added, nothing left out, nothing explained. Return every line, keyed by
its key.
"""
TRANSLATE_SCHEMA: dict[str, Any] = _obj({"lines": _arr(_obj({"key": _SHORT, "en": _STR}), 200)})
TRANSLATE_FORMAT = {"type": "json_schema", "name": "QuoteTranslations", "schema": TRANSLATE_SCHEMA, "strict": True}
# a page that says how to undo what a job leaves changed: the repair sees it beside the job's own pages
_RESTORE = re.compile(r"stekker (?:weer )?in (?:het|een) (?:geaard )?stopcontact|steek de stekker|(?:water)?kraan "
                      r"(?:weer |helemaal )?open|plug (?:the )?\w+ (?:back )?in|turn on the (?:water|tap)|open the "
                      r"(?:water )?tap|netzstecker (?:wieder )?einstecken|wasserhahn (?:wieder )?(?:ganz )?öffnen")
MAX_REPAIR_PAGES = 14

CLAUSE_SCHEMA: dict[str, Any] = _obj({"steps": _arr(_obj({
    "key": _SHORT, "step_id": _SHORT, "done_when": _arr(_obj({"id": _SHORT, "text_en": _STR}), 3),
}), 200)})
CLAUSE_FORMAT = {"type": "json_schema", "name": "StepClauses", "schema": CLAUSE_SCHEMA, "strict": True}
_GENERIC_STEP_ID = re.compile(r"(?:[a-z0-9_]+-)?(?:s|step)_?\d+")


def clause_input(job: dict[str, Any]) -> str | None:
    """A job's steps that lack clauses or carry a numbered id, as text; None when there are none."""
    if all(st.get("done_when") and not _GENERIC_STEP_ID.fullmatch(st["id"]) for st in job["steps"]):
        return None
    items = [{"key": f"{job['id']}/{i}", "instruction": st["text_en"], "sentence": st["quote"],
              "parts": st.get("parts", [])} for i, st in enumerate(job["steps"])]
    return (f"Job: {job['title_en']}. {len(items)} steps; return {len(items)} items, one per key, in order.\n"
            + json.dumps(items, ensure_ascii=False, indent=1))


def _clause_ok(text: str) -> bool:
    """A condition a judge can check: one short line, a statement, not an instruction to us."""
    t = text.strip()
    return 0 < len(t) <= 160 and "\n" not in t and not re.match(r"(?i)(describe|name|give|list|return)\b", t)


def merge_clauses(job: dict[str, Any], answer: dict[str, Any]) -> None:
    by_key = {a["key"]: a for a in answer.get("steps", [])}
    for i, st in enumerate(job["steps"]):
        a = by_key.get(f"{job['id']}/{i}")
        if a is None:
            continue
        if not st.get("done_when"):
            st["done_when"] = [d for d in a["done_when"] if _clause_ok(d["text_en"])]
        if _GENERIC_STEP_ID.fullmatch(st["id"]) and slug(a["step_id"]) != "x":
            st["id"] = slug(a["step_id"])


_PLAIN = frozenset([
    "the", "and", "is", "are", "its", "it", "out", "back", "in", "on", "of", "with", "from", "into", "place",
    "visible", "visibly", "fully", "completely", "no", "not", "any", "has", "been", "removed", "lifted", "open",
    "opened", "closed", "clean", "cleaned", "free", "position", "normal", "seated", "inside", "outside", "off",
    "each", "other", "one", "unit", "there", "remains", "shows", "appliance", "machine", "now", "still", "until",
    "all", "both", "their", "them", "they", "this", "that", "which", "where", "when", "then", "properly",
    "correctly", "firmly", "tight", "tightly", "again", "longer",
])
_CLAUSE_VERB = re.compile(r"\b(is|are|has|have|was|were|shows?|sits?|turns?|lines?|remains?|stays?)\b", re.I)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _PLAIN]


def clause_off_step(clause: str, step: dict[str, Any], part_names: dict[str, list[str]]) -> str | None:
    """Why a clause is not about its own step, or None. Its subject (the words before its verb) must share a word
    (by 5-letter stem, or inside one: plug/unplug) with the step's instruction, quote or the names of the parts it
    works on: a clause about the filter on a descale step fails."""
    parts = set(step.get("parts", []))
    if step.get("witness"):
        parts |= {x for x in (step["witness"]["part"], step["witness"].get("anchor")) if x}
    vocab = {w for t in (step["text_en"], step.get("text_manual", ""), step["quote"]) for w in _words(t)}
    vocab |= {w for p in parts for n in part_names.get(p, []) for w in _words(n)}
    m = _CLAUSE_VERB.search(clause)
    key = (_words(clause[:m.start()]) if m else []) or _words(clause)
    if key and not any(w[:5] == v[:5] or (len(w) > 3 and w in v) for w in key for v in vocab):
        return f"its subject ({' '.join(key)}) is not in its step"
    return None


def off_step_clauses(job: dict[str, Any], part_names: dict[str, list[str]]) -> list[tuple[dict[str, Any], str, str]]:
    return [(st, d["text_en"], why) for st in job["steps"] for d in st.get("done_when", [])
            if (why := clause_off_step(d["text_en"], st, part_names))]


async def _clauses(draft: dict[str, Any], *, lane: str, client: calls.Client, who: Who,
                   texts: list[str] | None = None) -> float:
    """One text-only call per job for the steps' done_when (the whole-manual draft leaves them empty; one call for
    all 49 Bosch steps answered only the first, and low effort mixed up keys). A job with a clause that is not about
    its own step is asked once more with that clause named; if it still has one, the job is marked to be dropped."""
    async def one(job: dict[str, Any], text: str) -> float:
        try:
            r = await client.respond("appliance_file", text, instructions=CLAUSE_INSTRUCTIONS, format=CLAUSE_FORMAT,
                                     lane=lane, **who)
        except calls.CallError:
            return 0.0  # the witness clause still covers a step with a part to see
        merge_clauses(job, r.parsed())
        return r.usd

    todo = [(j, t) for j in draft["jobs"] if (t := clause_input(j)) is not None]
    costs = list(await asyncio.gather(*(one(j, t) for j, t in todo)))
    part_names = {p["id"]: [p["name_en"], p["name_manual"]] for p in draft.get("parts", [])}
    retry = []
    first_off: dict[str, list[tuple[dict[str, Any], str, str]]] = {}
    for j in draft["jobs"]:
        off = off_step_clauses(j, part_names)
        if off:
            first_off[j["id"]] = off
            note = "".join(f"\nRejected, not about its own step: {st['id']}: {c!r}" for st, c, _ in off)
            pages = sorted({int(st["page"]) for st, _, _ in off if texts and 1 <= int(st["page"]) <= len(texts)})
            if texts and pages:  # the page each rejected step is printed on, so the clause can name what it shows
                note += "\nWrite each rejected step's clauses about what that step itself works on, named as on its " \
                        "page:" + "".join(f"\n--- page {n} ---\n{texts[n - 1]}" for n in pages)
            for st, _, _ in off:
                st["done_when"] = []
            retry.append((j, (clause_input(j) or "") + note))
    costs += await asyncio.gather(*(one(j, t) for j, t in retry))
    for j, _ in retry:
        off = off_step_clauses(j, part_names)
        off += [(st, c, why) for st, c, why in first_off[j["id"]] if not st["done_when"]]  # the retry gave none
        if off:
            j["_off_step"] = [(st["id"], int(st["page"]), c, why) for st, c, why in off]
    _normalise_ids(draft)
    return float(sum(costs))


@dataclass(frozen=True)
class Rejection:
    where: str  # e.g. "jobs[clean_filter].steps[rinse]", "red_zones[casing]", "jobs[x].interval"
    page: int
    quote: str
    reason: str
    dropped: str  # what left the card because of it


@dataclass
class IngestResult:
    card: dict[str, Any] | None
    rail: str
    rejected: list[Rejection] = field(default_factory=list)
    corrected: list[tuple[str, int, int]] = field(default_factory=list)  # (where, from, to)
    usd: float = 0.0
    model: str = ""
    error: str | None = None
    models: list[str] = field(default_factory=list)  # the model codes the manual prints, checked (K1)
    coverage: list[dict[str, Any]] = field(default_factory=list)  # each upkeep procedure: a job, dropped or missing
    lifted: list[dict[str, Any]] = field(default_factory=list)  # heat zones lifted by HEAT_LIFT, for the check record
    checklist: list[dict[str, Any]] = field(default_factory=list)  # each item of the kind's checklist and its fate
    cap_reached: bool = False  # a call was refused by the home's (or the run's) budget, before it was sent


def slug(s: str) -> str:
    out = re.sub(r"[^a-z0-9_-]+", "_", s.lower()).strip("_-")
    return (out or "x")[:60]


_SPACE_PUNCT = re.compile(r"\s+([.,;:!?])")
_NAME_SEP = re.compile(r"\s*\/\s*|\s+or\s+", re.IGNORECASE)


def tidy_quote(text: str) -> str:
    """The quote without the PDF extractor's space before punctuation ("eruit nemen ." → "eruit nemen."). The
    citation check reads punctuation as space, so the tidy quote still matches the page."""
    return _SPACE_PUNCT.sub(r"\1", text.strip())


def one_name(name: str) -> str:
    """A part's first name when the draft joined several ("fine sieve / micro sieve" → "fine sieve"): a name is read
    aloud, and the others stay in the card's keywords."""
    return re.split(_NAME_SEP, name.strip())[0].strip() if name.strip() else ""


# Words for the whole appliance. They are never a part's name: a casing the draft names "toestel" would make every line
# about the appliance ("Schakel het toestel uit") a line about its casing, held by G0.
_WHOLE = re.compile(r"(?:(?:het|de|the|das|die|der|le|la|l')\s*)?(?:toestel|apparaat|machine|ketel|cv-ketel|appliance|"
                    r"unit|device|boiler|gerät|geraet|maschine|appareil)", re.IGNORECASE)


def kept_names(en: str, local: str) -> tuple[str, str]:
    """A part's English and manual names, one each, with a word for the whole appliance left out."""
    en, local = one_name(en), one_name(local)
    return ("" if _WHOLE.fullmatch(en) else en), ("" if _WHOLE.fullmatch(local) else local)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(¡•])")


def sentences(text: str) -> int:
    return len([s for s in _SENTENCE_END.split(text.strip()) if s])


_BOX_MARK = re.compile(r"^[^\w\s\"'(]+\s*")  # the symbol a manual sets before a step or a warning box
_PAGE_NO = re.compile(r"\d{1,3}")


def _box_lines(page: str, keep_hyphen: bool) -> tuple[str, list[tuple[int, int, bool]]]:
    """The page as one line of text, a word broken at a line end joined back, with each printed line's (start, end,
    starts with a symbol) in that text."""
    text, spans = "", []
    for raw in page.split("\n"):
        line = raw.strip()
        mark = bool(_BOX_MARK.match(line))
        line = _BOX_MARK.sub("", line)
        if not line:
            continue
        if text.endswith("-") and line[:1].islower():
            text = text if keep_hyphen else text[:-1]
        elif text:
            text += " "
        spans.append((len(text), len(text) + len(line), mark))
        text += line
    return text, spans


_BOX_TITLE = 60  # a warning box's own title, printed at its start: one sentence, on its own line (or two, wrapped)


def box_sentences(page: str, quote: str) -> int | None:
    """None unless the quote is a whole warning box of the manual: it opens with a warning, is printed verbatim on
    this one page, starts where a printed line starts after a finished sentence or a symbol, and ends where its last
    line ends with nothing of the box after it (the next line opens with a symbol, or is the page number or the end).
    Else its sentences, not counting the box's own title: its first sentence when that is short and stands on its
    own printed line at the box's start (wrapped onto a second at most), the box's words starting on the next."""
    q = " ".join(quote.split())
    parts = [x for x in _SENTENCE_END.split(q) if x]
    if not q or not jobcheck.warns(parts[0]):
        return None
    for keep in (False, True):
        text, spans = _box_lines(page, keep)
        starts = {a: i for i, (a, _, _) in enumerate(spans)}
        ends = {b: i for i, (_, b, _) in enumerate(spans)}
        at = text.find(q)
        while at >= 0:
            i, j = starts.get(at), ends.get(at + len(q))
            if i is not None and j is not None:
                opened = spans[i][2] or i == 0 or text[spans[i - 1][1] - 1] in ".!?:"
                rest = [k for k in range(j + 1, len(spans)) if not _PAGE_NO.fullmatch(text[spans[k][0]:spans[k][1]])]
                if opened and (not rest or spans[rest[0]][2]):
                    t = ends.get(at + len(parts[0]))
                    titled = len(parts) > 1 and len(parts[0]) <= _BOX_TITLE and t is not None and t - i <= 1
                    return len(parts) - titled
            at = text.find(q, at + 1)
    return None


def warning_box(page: str, quote: str) -> bool:
    return box_sentences(page, quote) is not None


def within_cap(page: str | None, quote: str) -> bool:
    """At most MAX_SENTENCES sentences; a whole warning box on its one page may run to WARNING_BOX_SENTENCES, its
    own title line not counted."""
    if sentences(quote) <= MAX_SENTENCES:
        return True
    n = box_sentences(page, quote) if page is not None else None
    return n is not None and n <= WARNING_BOX_SENTENCES


def whole_box(page: str, quote: str) -> str | None:
    """The whole warning box a quote sits inside, as printed on the page (from the symbol that opens it to the line
    before the next symbol or the page number), when the quote is only part of it and the whole box passes the box
    rule (warning_box, within_cap). None otherwise: a quote outside any box, or a box that fails the rule."""
    text, spans = _box_lines(page, False)
    q = " ".join(quote.split())
    at = text.find(q) if q else -1
    if at < 0:
        return None
    i = max(k for k, (a, _, _) in enumerate(spans) if a <= at)
    s = next((k for k in range(i, -1, -1) if spans[k][2]), None)
    if s is None:
        return None
    e = next((k for k in range(i + 1, len(spans))
              if spans[k][2] or _PAGE_NO.fullmatch(text[spans[k][0]:spans[k][1]])), len(spans))
    box = text[spans[s][0]:spans[e - 1][1]]
    return box if box != q and warning_box(page, box) and within_cap(page, box) else None


_TIMES = {"twee": 2, "twice": 2, "zweimal": 2, "drie": 3, "three": 3, "dreimal": 3, "vier": 4, "four": 4,
          "viermal": 4, "zes": 6, "six": 6, "sechsmal": 6}
_PER = re.compile(r"\b(twee|drie|vier|zes|twice|three|four|six|zweimal|dreimal|viermal|sechsmal|[2-6])\s*"
                  r"(?:keer|maal|x|times|mal)?\s+(?:per|a|in|een|pro|im|every|each|elk)\s+(?:het |de |the |an? )?"
                  r"(jaar|year|jahr|maand|month|monat)\b", re.IGNORECASE)


def per_period(interval: dict[str, Any]) -> None:
    """"Twee keer per jaar" is every 6 months, never every 2 years: when the quote counts times per year (or per
    month) and the draft read the count as the step between, code sets the interval from the quote."""
    m = _PER.search(interval.get("quote") or "")
    if not m:
        return
    n = int(m.group(1)) if m.group(1).isdigit() else _TIMES[m.group(1).lower()]
    year = m.group(2).lower() in ("jaar", "year", "jahr")
    if year and 12 % n == 0:
        interval["every"], interval["unit"] = 12 // n, "month"
    elif not year and n in (2, 4):
        interval["every"], interval["unit"] = 4 // n, "week"


def short_row_quotes(draft: dict[str, Any]) -> None:
    """A fault row's quote cut to its first two sentences (what is wrong, and the first thing to do): the builder
    quotes the whole table line, and a line with three remedies is more than a card may quote. Only rows: a step's
    quote that is too long still fails."""
    for r in draft.get("troubleshooting", []):
        for f in ("quote", "quote_en"):
            if isinstance(r.get(f), str) and sentences(r[f]) > MAX_SENTENCES:
                r[f] = " ".join([x for x in _SENTENCE_END.split(r[f].strip()) if x][:MAX_SENTENCES])


def witness_clause(witness: dict[str, Any], part_names: dict[str, str]) -> dict[str, Any]:
    """The clause a witnessed step can always be judged on, when the draft gave none. "In place", not "back in
    place": the step may only look at the part ("Check the filters")."""
    part = part_names.get(witness["part"], witness["part"].replace("_", " "))
    anchor = part_names.get(witness["anchor"] or "", "")
    verb = "are" if re.search(r"[^s]s$", part.split()[-1]) else "is"
    if witness["expect"] == "absent":
        text = f"The {part} {verb} out" + (f" of the {anchor}" if anchor else "") + "."
    else:
        text = f"The {part} {verb} in place" + (f" in the {anchor}" if anchor else "") + "."
    return {"id": f"{witness['part']}_{witness['expect']}"[:60], "text": {"en": text}, "quote_ref": None}


MIN_SECTION_PAGES = 4  # a language section shorter than this is not a manual on its own: the whole PDF is sent


def language_pages(texts: list[str], lang: str) -> list[int] | None:
    """The PDF pages (1-based) the draft reads in a manual of several languages: the pages in the card's language and
    the pages with no language (figures, the cover). None, for the whole PDF, when nothing is left out or what would
    be sent is too short to be the manual. Electrolux KNA6LE18S's baseline card was built from its Catalan section."""
    langs = [page_lang(t) for t in texts]
    keep = [n for n, la in enumerate(langs, 1) if la in (lang, None)]
    if len(keep) == len(texts) or sum(la == lang for la in langs) < MIN_SECTION_PAGES:
        return None
    return keep


def pdf_pages(pdf: Path, keep: list[int]) -> bytes:
    """A PDF of only the `keep` pages, in order: the draft cites its page k, which is the manual's page keep[k-1]."""
    writer = PdfWriter()
    reader = PdfReader(pdf)
    for n in keep:
        writer.add_page(reader.pages[n - 1])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def page_back(keep: list[int] | None, page: int) -> int:
    """The manual's page for the draft's page `page`; one outside the PDF sent is left for the citation check."""
    return keep[page - 1] if keep and 1 <= page <= len(keep) else page


def request_input(pdf_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    data = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode()
    return [{"role": "user", "content": [
        {"type": "input_file", "filename": filename, "file_data": data},
        {"type": "input_text", "text": "Build the appliance file for this manual."},
    ]}]


class _Checker:
    """Runs the citation check over the model's draft and keeps what passes."""

    def __init__(self, pages: list[Page]) -> None:
        self.texts = [p.text for p in pages]
        self.ocr = frozenset(p.number for p in pages if p.ocr)
        self.corrected: list[tuple[str, int, int]] = []

    def check(self, where: str, page: int, quote: str) -> CitationResult:
        if not within_cap(self.texts[page - 1] if 1 <= page <= len(self.texts) else None, quote):
            return CitationResult(False, 0.0, "too_long")
        r = verify(self.texts, page, quote, ocr_pages=self.ocr)
        if r.ok and r.corrected_from is not None and r.page is not None:
            self.corrected.append((where, r.corrected_from, r.page))
        return r

    def cite(self, where: str, o: dict[str, Any]) -> CitationResult:
        """Check o's quote on its page; a quote too short to check is widened to the manual's own sentence around
        it (citation.widen) and checked again, the same check, and a step's passing quote inside a warning box to
        that whole box (whole_box), so the warning is carried whole. Its translation covered the short quote only: it is
        cleared here, and the whole widened quote is translated once every quote is checked (`_translate`)."""
        r = self.check(where, int(o["page"]), o["quote"])
        page = int(o["page"])
        if r.reason == "too_short" and 1 <= page <= len(self.texts) and (
                wide := widen(self.texts[page - 1], o["quote"])):
            again = self.check(where, page, wide)
            if again.ok:
                o["quote"] = wide
                if "quote_en" in o:
                    o["quote_en"] = ""
                r = again
        if r.ok and ".steps[" in where and 1 <= page <= len(self.texts) and (
                box := whole_box(self.texts[page - 1], o["quote"])):
            again = self.check(where, page, box)
            if again.ok:
                o["quote"] = box
                if "quote_en" in o:
                    o["quote_en"] = ""
                r = again
        return r


def _cites(draft: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every (where, object holding page+quote) in the draft, in a stable order."""
    out: list[tuple[str, dict[str, Any]]] = []
    for z in draft.get("red_zones", []):
        out.append((f"red_zones[{z['id']}]", z))
    for j in draft.get("jobs", []):
        if j.get("interval"):
            out.append((f"jobs[{j['id']}].interval", j["interval"]))
        if j.get("duration"):
            out.append((f"jobs[{j['id']}].duration", j["duration"]))
        for s in j.get("steps", []):
            if s.get("origin") != "fixit":  # ours, uncited: there is no quote to check
                out.append((f"jobs[{j['id']}].steps[{s['id']}]", s))
        for i, a in enumerate(j.get("after", [])):
            out.append((f"jobs[{j['id']}].after[{i}]", a))
    for r in draft.get("troubleshooting", []):
        out.append((f"troubleshooting[{r['id']}]", r))
        for i, t in enumerate(r.get("prevented_by", [])):
            out.append((f"troubleshooting[{r['id']}].prevented_by[{i}]", t))
        if r.get("repeat"):
            out.append((f"troubleshooting[{r['id']}].repeat", r["repeat"]))
    return out


def boiler_ids(draft: dict[str, Any]) -> None:
    """On a boiler, a job's id comes from its task (top_up, top_up_2, ...), never from the model's own id, and every
    `after` link follows it. A job with task other keeps its id, and assemble drops and lists it."""
    if draft.get("appliance_kind") != "boiler":
        return
    renamed: dict[str, str] = {}
    used: set[str] = set()
    for j in draft.get("jobs", []):
        task = j.get("task", "other")
        if task not in BOILER_JOBS:
            continue
        new, n = task, 1
        while new in used:
            n += 1
            new = f"{task}_{n}"
        used.add(new)
        renamed[j["id"]] = new
        j["id"] = new
    for j in draft.get("jobs", []):
        for a in j.get("after", []):
            a["job_id"] = renamed.get(a["job_id"], a["job_id"])
    for r in draft.get("troubleshooting", []):
        r["job_id"] = renamed.get(r["job_id"], r["job_id"])
        for t in r.get("prevented_by", []):
            t["job_id"] = renamed.get(t["job_id"], t["job_id"])
    for u in [*draft.get("upkeep_procedures", []), *draft.get("checklist", [])]:
        if u.get("job_id"):
            u["job_id"] = renamed.get(u["job_id"], u["job_id"])


def job_steps(job: dict[str, Any]) -> list[jobcheck.Step]:
    return [jobcheck.Step(str(s.get("text_en", "")), str(s.get("quote", "")), int(s.get("page") or 0))
            for s in job.get("steps") or []]


def drafted_fixit(draft: dict[str, Any]) -> None:
    """A step the model marks as ours is dropped and listed (assemble): only code appends one (append_restores)."""
    for j in draft.get("jobs", []):
        ours = [s for s in j.get("steps", []) if s.get("origin") == "fixit" or "restores" in s]
        if ours:
            j["steps"] = [s for s in j["steps"] if s not in ours]
            j["_drafted_fixit"] = [s["id"] for s in ours]


def drop_repeated_steps(draft: dict[str, Any]) -> None:
    """A step that says again what the step before it said is dropped (code's call, never the job)."""
    for j in draft.get("jobs", []):
        dup = set(jobcheck.duplicate_steps(job_steps(j)))
        if dup:
            j["steps"] = [s for i, s in enumerate(j["steps"]) if i not in dup]


# Off until an independent review of what it changes on real cards: (a) asks the builder for narrower heat zones,
# (b) lifts a heat zone from a part a job works on when every such job cites the manual's own cool-down from its own
# procedure. Heat only: a mains, gas, water or pressure zone is never lifted.
HEAT_LIFT = False
HEAT_NARROW = """
- heat.hot_water is for a part that is itself hot (the heating element, the door glass in a hot wash, a hot
  surface). Not for a hose, filter, cap or flap the user handles in a job where the manual says to let the water
  cool down first: there the job carries the manual's cool-down step.
"""
_COOL = re.compile(r"afkoel|koud is|cool(?:s|ed)? down|cooled|let (?:it|the [\w ]{1,20}) cool|abkühl|abgekühlt|"
                   r"refroidi", re.IGNORECASE)


def instructions() -> str:
    return INSTRUCTIONS + HEAT_NARROW if HEAT_LIFT else INSTRUCTIONS


def _cools(quote: dict[str, Any]) -> bool:
    return bool(_COOL.search(quote.get("text") or "") or _COOL.search(quote.get("en") or ""))


def heat_lifts(card: dict[str, Any]) -> list[dict[str, Any]]:
    """The heat zones a card could lift, with what lifts each: a zone whose rule is heat.*, on a part at least one
    job works on, where every such job has a step quoting the manual's own cool-down on a page inside that job's
    procedure (its first to its last cited page). One job without it keeps the zone. Each: {zone, rule_id, part_id,
    jobs: [{job, step, page}]}, for the check record and the "how this card was checked" panel."""
    out = []
    for z in card.get("red_zones", []):
        if ".heat." not in f".{z['rule_id']}":
            continue
        jobs = [j for j in card.get("jobs", [])
                if any(z["part_id"] in s["parts"] or z["id"] in s["zones"] for s in j["steps"])]
        cited = []
        for j in jobs:
            cool = next((s for s in j["steps"] if _cools(s.get("quote") or {})), None)
            # the procedure's pages are its other steps': a cool-down cited from elsewhere does not stretch them
            span = [s["page"] for s in j["steps"] if s.get("origin") != "fixit" and s is not cool]
            if cool is None or not span or not min(span) <= cool["quote"]["page"] <= max(span):
                break
            cited.append({"job": j["id"], "step": cool["id"], "page": cool["quote"]["page"]})
        else:
            if jobs:
                out.append({"zone": z["id"], "rule_id": z["rule_id"], "part_id": z["part_id"], "jobs": cited})
    return out


def lift_heat(card: dict[str, Any], lifts: list[dict[str, Any]]) -> None:
    """Takes each lifted zone off the card: the zone, its id on the steps, and the part's red_zone kind when no other
    zone holds the part."""
    gone = {x["zone"] for x in lifts}
    card["red_zones"] = [z for z in card["red_zones"] if z["id"] not in gone]
    held = {z["part_id"] for z in card["red_zones"]}
    for j in card["jobs"]:
        for s in j["steps"]:
            s["zones"] = [z for z in s["zones"] if z not in gone]
    for p in card["parts"]:
        if p["kind"] == "red_zone" and p["id"] in {x["part_id"] for x in lifts} and p["id"] not in held:
            p["kind"] = "allowed"


# Our own restore step (contracts 1.27, origin fixit, added_by ingest): fixed words, never a model's. Ingest adds one
# only where the whole-job check fails solely for what our fixed words undo and the job's own pages print no line that
# does (jobcheck.own_restores): the mains, the water or the power left off, restored just before the first later step
# that needs them or at the end, and a part taken out and never put back, at the end. The phone marks it "Our step,
# not from the manual"; the card is not shared until a person accepts it (check_pr.fixit_steps, tools.confirm_codes).
RESTORE_APPEND = True
RESTORE_TEXT = {
    "water_supply": {"en": "Turn the water tap back on.", "nl": "Draai de waterkraan weer open."},
    "mains": {"en": "Plug the appliance back in.", "nl": "Steek de stekker weer in het stopcontact."},
    "power": {"en": "Switch the appliance back on.", "nl": "Zet het apparaat weer aan."},
}
RESTORE_PART = {"en": "Put the {en} back in place.", "nl": "Plaats terug: {manual}."}


def append_restores(draft: dict[str, Any], failures: dict[str, list[jobcheck.Failure]],
                    texts: list[str]) -> list[str]:
    """Each failing job that jobcheck.own_restores allows gets our fixed restore steps, when the step each one
    undoes is a power-off step and none after it is (the contract's rule); never for a part a red zone holds or a
    step in one. Its failures are then checked again. The ids of the jobs it changed."""
    if not RESTORE_APPEND:
        return []
    lang = draft["manual_lang"]
    parts = {p["id"]: p for p in draft.get("parts", [])}
    held = {z["part_id"] for z in draft.get("red_zones", [])} | {i for i, p in parts.items() if p.get("kind") ==
                                                                 "red_zone"}
    changed = []
    for j in draft.get("jobs", []):
        steps = j.get("steps") or []
        kept = {i for i, st in enumerate(steps) if st.get("zones") or held & set(st.get("parts") or [])}
        ours = jobcheck.own_restores(job_steps(j), failures.get(j["id"], []), texts, kept) \
            if j["id"] in failures else []
        if not ours or any(steps[i].get("power") != "off" for _, i, _ in ours):
            continue
        added: list[tuple[int, dict[str, Any]]] = []
        for what, i, before in ours:
            if what in RESTORE_TEXT:
                words = RESTORE_TEXT[what]
            elif (p := next((parts[x] for x in steps[i].get("parts") or [] if x in parts
                             and what in str(parts[x].get("name_en", "")).lower()), None)) is not None:
                words = {k: v.format(en=p["name_en"], manual=p.get("name_manual") or p["name_en"])
                         for k, v in RESTORE_PART.items()}
            else:
                added = []
                break
            at = len(steps) if before is None else before
            added.append((at, {"id": slug(f"{j['id']}_restore_{what}"), "origin": "fixit", "added_by": "ingest",
                               "restores": steps[i]["id"], "text_en": words["en"],
                               "text_manual": words.get(lang, words["en"]), "page": 0, "quote": ""}))
        # ours comes after every power-off step of its job, and none restores a step twice (the contract)
        if not added or any(st.get("power") == "off" for at, _ in added for st in steps[at:]) \
                or len({o["restores"] for _, o in added}) != len(added):
            continue
        for _, (at, o) in sorted(enumerate(added), key=lambda x: (x[1][0], x[0]), reverse=True):
            steps.insert(at, o)  # from the back, so each lands before the step it was meant for, in its order
        changed.append(j["id"])
    return changed


def for_repair(draft: dict[str, Any], failures: dict[str, list[jobcheck.Failure]],
               texts: list[str]) -> dict[str, list[jobcheck.Failure]]:
    """The failures the job repair is asked to mend: a job's "left off, never back on" that append_restores will
    undo is left out, so the repair never gives a job up, warnings and all, for a plug-back line its manual does not
    print. The final gate still reads the whole job."""
    jobs = {j["id"]: j for j in draft.get("jobs", [])}
    out = {}
    for jid, fails in failures.items():
        off = [f for f in fails if jobcheck.left_off(f)]
        steps = jobs[jid].get("steps") or []
        pairs = jobcheck.restorable(job_steps(jobs[jid]), off, texts) if RESTORE_APPEND and off else []
        if pairs and all(steps[i].get("power") == "off" for _, i in pairs):
            fails = [f for f in fails if f not in off]
        if fails:
            out[jid] = fails
    return out


_ID_LIKE = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)+")
_EMPTY_CLAUSE = re.compile(r"(?:you are|you're|we are|it is|je bent|u bent|het is) (?:done|finished|klaar|gereed)\.?|"
                           r"(?:done|finished|klaar|gereed|ok)\.?", re.IGNORECASE)
_RUNS = re.compile(r"\b(?:start|run|select|choose|set|turn on|switch on)s?\b.{0,40}\b(?:programme|program|cycle|"
                   r"wash|cleaning|rinse|spin)\b", re.IGNORECASE)


def job_defects(draft: dict[str, Any]) -> dict[str, list[jobcheck.Failure]]:
    """What code can see is wrong in a job's own fields: a step id another step on the card also has, two clauses of
    one step with one id, an id where the person reads text, a clause that says nothing ("You are done."), and a
    step that runs a programme while it says the power is off."""
    seen = Counter(str(s["id"]) for j in draft.get("jobs", []) for s in j.get("steps") or [])
    ids = {str(j["id"]) for j in draft.get("jobs", [])} | set(seen)
    out: dict[str, list[jobcheck.Failure]] = {}
    for j in draft.get("jobs", []):
        fails = []
        for title in (j.get("title_en", ""), j.get("title_manual", "")):
            if title in ids or _ID_LIKE.fullmatch(title.strip()):
                fails.append(jobcheck.Failure("id_as_text", "the title is an id"))
        for n, s in enumerate(j.get("steps") or [], 1):
            if seen[str(s["id"])] > 1:
                fails.append(jobcheck.Failure("duplicate_id", f"step {n}'s id is another step's too"))
            said = [s.get("text_en", ""), s.get("text_manual", "")]
            clauses = s.get("done_when") or []
            if len({str(d["id"]) for d in clauses}) < len(clauses):
                fails.append(jobcheck.Failure("duplicate_id", f"two clauses of step {n} share an id"))
            for d in clauses:
                text = str(d.get("text_en", "")).strip()
                if _EMPTY_CLAUSE.fullmatch(text):
                    fails.append(jobcheck.Failure("empty_clause", f"a clause of step {n} says nothing"))
                said.append(text)
            if any(t.strip() in ids or _ID_LIKE.fullmatch(t.strip()) for t in said if t.strip()):
                fails.append(jobcheck.Failure("id_as_text", f"step {n} shows an id as text"))
            if s.get("power") == "off" and s.get("origin") != "fixit" and _RUNS.search(s.get("text_en", "")):
                fails.append(jobcheck.Failure("power_off_run", f"step {n} runs a programme with the power off"))
        if fails:
            out[str(j["id"])] = list(dict.fromkeys(fails))
    return out


def card_duplicate_ids(card: dict[str, Any]) -> list[str]:
    """Ids the card uses twice where one id must name one thing: jobs, steps (across the card), quotes, parts, red
    zones and fault rows. Any is a build failure: a reference to it would be ambiguous."""
    groups = {"job": [j["id"] for j in card.get("jobs", [])],
              "step": [s["id"] for j in card.get("jobs", []) for s in j.get("steps", [])],
              "quote": [q["id"] for q in card.get("quotes", [])], "part": [p["id"] for p in card.get("parts", [])],
              "red zone": [z["id"] for z in card.get("red_zones", [])],
              "fault row": [r["id"] for r in card.get("troubleshooting", [])]}
    return [f"{kind} id {i!r} is used {n} times" for kind, got in groups.items()
            for i, n in sorted(Counter(map(str, got)).items()) if n > 1]


def whole_job_failures(draft: dict[str, Any], texts: list[str], gate: bool = False,
                       defects: bool = True) -> dict[str, list[jobcheck.Failure]]:
    """Each job the card would keep that fails the whole-job check, with its failures; with `gate`, also the
    gate's (jobcheck.gate), the final say after every repair, and with `defects` the job's own field defects, which
    no step the repair adds can mend."""
    kind = draft.get("appliance_kind", "other")
    titles = {j["id"]: j["title_en"] or j["title_manual"] for j in draft.get("jobs", [])}
    out = {}
    found = job_defects(draft) if gate and defects else {}
    for j in draft.get("jobs", []):
        if not j.get("steps") or (kind == "boiler" and j.get("task") not in BOILER_JOBS):
            continue
        others = [t for i, t in titles.items() if i != j["id"]]
        fails = jobcheck.check(job_steps(j), texts, kind, others)
        if gate:
            theirs = [s for o in draft["jobs"] if o["id"] != j["id"] for s in job_steps(o)]
            fails += [f for f in jobcheck.gate(job_steps(j), texts, kind, theirs) + found.get(j["id"], [])
                      if f not in fails]
        if fails:
            out[j["id"]] = fails
    return out


_ON_PAGE = re.compile(r"\bon page (\d+)")


def repair_pages(job: dict[str, Any], failures: list[jobcheck.Failure], texts: list[str]) -> list[int]:
    """The pages the repair reads for one job, most needed first: each page a failure names (where the dropped
    warning is printed), then the pages around the job's own steps, then, when it leaves something off, pages that
    say how to undo it."""
    named = [int(m.group(1)) for f in failures for m in _ON_PAGE.finditer(f.detail)]
    near = sorted({n for s in job["steps"] for n in range(int(s["page"]) - 1, int(s["page"]) + 2)})
    undo = [n for n, t in enumerate(texts, 1) if _RESTORE.search(t.lower())][:4] if any(
        f.code == "leaves_state" for f in failures) else []
    return list(dict.fromkeys(n for n in named + near + undo if 1 <= n <= len(texts)))


def repair_groups(jobs: list[dict[str, Any]], failures: dict[str, list[jobcheck.Failure]],
                  texts: list[str]) -> list[list[dict[str, Any]]]:
    """The failing jobs in groups whose pages fit one repair call, so no job's pages are cut for another's."""
    groups: list[tuple[list[dict[str, Any]], set[int]]] = []
    for j in jobs:
        mine = set(repair_pages(j, failures[j["id"]], texts)[:MAX_REPAIR_PAGES])
        for g, pages in groups:
            if len(pages | mine) <= MAX_REPAIR_PAGES:
                g.append(j)
                pages |= mine
                break
        else:
            groups.append(([j], mine))
    return [g for g, _ in groups]


def job_repair_input(jobs: list[dict[str, Any]], failures: dict[str, list[jobcheck.Failure]], texts: list[str],
                     kind: str) -> str:
    pages = sorted({n for j in jobs for n in repair_pages(j, failures[j["id"]], texts)[:MAX_REPAIR_PAGES]})
    return json.dumps({"appliance_kind": kind, "jobs": [
        {"key": j["id"], "title": j["title_en"], "failures": [str(f) for f in failures[j["id"]]],
         "steps": [{"n": i + 1, "text_en": s["text_en"], "page": int(s["page"]), "quote": s["quote"]}
                   for i, s in enumerate(j["steps"])]} for j in jobs],
        "pages": [{"page": n, "text": texts[n - 1]} for n in pages]}, ensure_ascii=False)


def apply_job_fix(job: dict[str, Any], fix: dict[str, Any]) -> bool:
    """The model's proposed edit, applied to the job's steps; False when it proposes none. The edited job is checked
    again (its new quotes and the whole job) before it can be kept."""
    if not fix.get("fixable") or not (fix.get("add") or fix.get("remove")):
        return False
    gone = {int(n) - 1 for n in fix.get("remove", [])}
    order = [(float(i), s) for i, s in enumerate(job["steps"]) if i not in gone]
    for k, a in enumerate(fix.get("add", [])):
        order.append((int(a["before"]) - 1 - 0.5 + k * 0.001, dict(a["step"])))
    job["steps"] = [s for _, s in sorted(order, key=lambda x: x[0])]
    job.pop("_off_step", None)
    return True


async def _job_repair(draft: dict[str, Any], failures: dict[str, list[jobcheck.Failure]], texts: list[str], *,
                      lane: str, client: calls.Client, who: Who) -> tuple[dict[str, dict[str, Any]], float]:
    jobs = [j for j in draft["jobs"] if j["id"] in failures]

    async def one(group: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], float]:
        try:
            r = await client.respond("appliance_file", job_repair_input(group, failures, texts,
                                                                        draft["appliance_kind"]),
                                     instructions=JOB_REPAIR_INSTRUCTIONS, format=JOB_REPAIR_FORMAT, lane=lane,
                                     **who)
        except calls.CallError:
            return {}, 0.0  # no proposal: the group's failing jobs are dropped and listed
        return {f["key"]: f for f in r.parsed().get("jobs", [])}, r.usd

    fixes: dict[str, dict[str, Any]] = {}
    usd = 0.0
    for got, spent in await asyncio.gather(*(one(g) for g in repair_groups(jobs, failures, texts))):
        fixes |= got
        usd += spent
    return fixes, usd


def title_problems(en: str, local: str, *, brand: str, model: str, lang: str) -> list[str]:
    """What is wrong with a job's titles once code has cleaned them: [] for a plain task title."""
    out = titles.problems(titles.clean(en, model=model, brand=brand), model=model, brand=brand)
    if lang != "en" and local.strip():
        out += titles.problems(titles.clean(local, model=model, brand=brand), model=model, brand=brand,
                               english=False)
    return out


def title_input(jobs: list[dict[str, Any]], lang: str) -> str:
    items = [{"key": j["id"], "kind": j["kind"], "title_en": j["title_en"], "title_manual": j["title_manual"],
              "fault_codes": j["fault_codes"], "steps": [st["text_en"] for st in j["steps"]][:8]} for j in jobs]
    return f"Manual language: {lang}. {len(items)} jobs.\n" + json.dumps(items, ensure_ascii=False, indent=1)


async def _titles(draft: dict[str, Any], *, brand: str, model: str, lane: str,
                  client: calls.Client, who: Who) -> tuple[dict[str, dict[str, str]], float]:
    """A new title proposed for each job whose title is not a plain task even after code cleans it. Only a proposal:
    `pick_title` keeps it only if it passes the same check."""
    lang = draft["manual_lang"]
    jobs = [j for j in draft["jobs"]
            if title_problems(j["title_en"], j["title_manual"], brand=brand, model=model, lang=lang)]
    if not jobs:
        return {}, 0.0
    try:
        r = await client.respond("appliance_file", title_input(jobs, lang), instructions=TITLE_INSTRUCTIONS,
                                 format=TITLE_FORMAT, lane=lane, effort="low", **who)
    except calls.CallError:
        return {}, 0.0  # the cleaned draft titles stand
    return {t["key"]: t for t in r.parsed().get("jobs", [])}, r.usd


async def _translate(draft: dict[str, Any], bad: dict[str, CitationResult], *, lane: str, client: calls.Client,
                     who: Who) -> float:
    """The English under each passing quote that has none: a quote widened to the manual's whole sentence (its
    draft translation covered the short quote only, so the check cleared it) or one the draft left untranslated.
    One call for all of them; on a failed call the quote shows without English, as before."""
    if draft["manual_lang"] == "en":
        return 0.0
    todo = {w: o for w, o in _cites(draft) if w not in bad and o.get("quote", "").strip()
            and not (o.get("quote_en") or "").strip()}
    if not todo:
        return 0.0
    keys = {f"q{i}": w for i, w in enumerate(todo)}
    lines = [{"key": k, "text": todo[w]["quote"]} for k, w in keys.items()]
    try:
        r = await client.respond("appliance_file", f"Manual language: {draft['manual_lang']}. {len(lines)} lines.\n"
                                 + json.dumps(lines, ensure_ascii=False, indent=1), instructions=TRANSLATE_INSTRUCTIONS,
                                 format=TRANSLATE_FORMAT, lane=lane, effort="low", **who)
    except calls.CallError:
        return 0.0
    for t in r.parsed().get("lines", []):
        if t.get("key") in keys and t.get("en", "").strip():
            todo[keys[t["key"]]]["quote_en"] = t["en"].strip()
    return r.usd


def pick_title(j: dict[str, Any], alt: dict[str, str] | None, *, brand: str, model: str, lang: str) -> None:
    """The job's titles, cleaned by code: the draft's if they pass, else the proposed ones if those pass, else the
    draft's as cleaned. A fault code the draft gave stays in the title either way."""
    candidates = [(j["title_en"], j["title_manual"])]
    if alt:
        candidates.append((alt["title_en"], alt["title_manual"] if lang != "en" else ""))
    for en, local in candidates:
        if not title_problems(en, local, brand=brand, model=model, lang=lang):
            break
    else:
        en, local = candidates[0]
    j["title_en"] = titles.clean(en, model=model, brand=brand)
    j["title_manual"] = titles.clean(local, model=model, brand=brand) if local.strip() else local


_GENERIC_ID = re.compile(r"(autogen|row|id|x|item|fault|code)?[_-]?\d*")


def _unique(base: str, seen: set[str]) -> str:
    out, n = base, 1
    while out in seen:
        n += 1
        out = f"{base[:56]}_{n}"
    seen.add(out)
    return out


def _normalise_ids(draft: dict[str, Any]) -> None:
    for p in draft.get("parts", []):
        p["id"] = slug(p["id"])
    for z in draft.get("red_zones", []):
        z["id"], z["part_id"] = slug(z["id"]), slug(z["part_id"])
    # every id the citation check keys on is unique before it runs: two rows or jobs under one id would share one
    # result, and so one page
    rows_seen: set[str] = set()
    for r in draft.get("troubleshooting", []):
        rid = slug(r["id"])
        if _GENERIC_ID.fullmatch(rid):  # the model's placeholder: named from the row's code or words instead
            words = r["codes"][0] if r.get("codes") else r.get("indicator_en") or r.get("symptom_en") or ""
            rid = slug(f"fault_{words}" if r.get("codes") else words)[:40] or rid
        r["id"] = _unique(rid, rows_seen)
        r["job_id"] = slug(r["job_id"]) if r.get("job_id") else ""
        if r.get("repeat") and not str(r["repeat"].get("quote", "")).strip():
            r["repeat"] = None  # an empty one is the draft's "none", not a quote that failed
        for t in r.get("prevented_by", []):
            t["job_id"] = slug(t["job_id"])
    for u in draft.get("upkeep_procedures", []):
        u["job_id"] = slug(u["job_id"]) if u.get("job_id") else ""
    jobs_seen: set[str] = set()
    seen: set[str] = set()  # step ids are unique per card: a reused one is qualified by its job (job-step)
    for j in draft.get("jobs", []):
        j["id"] = _unique(slug(j["id"]), jobs_seen)
        for opt in ("interval", "duration"):
            if j.get(opt) and not str(j[opt].get("quote", "")).strip():
                j[opt] = None
        for a in j.get("after", []):
            a["job_id"] = slug(a["job_id"])
        for i, s in enumerate(j.get("steps", [])):
            sid = slug(s["id"])
            if sid in seen:
                sid = f"{j['id']}-{sid}"[:60]
            if sid in seen:
                sid = f"{sid[:56]}_{i + 1}"
            s["id"] = sid
            seen.add(sid)
            s["parts"] = [slug(x) for x in s.get("parts", [])]
            if s.get("witness"):
                w = s["witness"]
                w["part"], w["anchor"] = slug(w["part"]), slug(w["anchor"]) if w.get("anchor") else None
            s["zones"] = [slug(x) for x in s.get("zones", [])]
            for k, d in enumerate(s.get("done_when", [])):
                d["id"] = slug(d.get("id") or f"c{k + 1}")


FILE_FORMAT = {"type": "json_schema", "name": "ApplianceFile", "schema": APPLIANCE_FILE_SCHEMA, "strict": True}
REPAIR_FORMAT = {"type": "json_schema", "name": "QuoteRepair", "schema": REPAIR_SCHEMA, "strict": True}


def repair_input(failed: list[tuple[str, dict[str, Any]]], texts: list[str]) -> str:
    items = []
    for where, obj in failed:
        page = int(obj["page"])
        near = {n: texts[n - 1] for n in (page - 1, page, page + 1) if 1 <= n <= len(texts)}
        items.append({"key": where, "cited_page": page, "quote": obj["quote"],
                      "pages": [{"page": n, "text": t} for n, t in near.items()]})
    return json.dumps({"items": items}, ensure_ascii=False)


async def _repair(failed: list[tuple[str, dict[str, Any]]], texts: list[str], *, lane: str,
                  client: calls.Client, who: Who) -> tuple[dict[str, tuple[int, str | None]], float]:
    r = await client.respond("appliance_file", repair_input(failed, texts), instructions=REPAIR_INSTRUCTIONS,
                             format=REPAIR_FORMAT, effort="low", lane=lane, **who)
    fixes = {f["key"]: (int(f["page"]), f["quote"]) for f in r.parsed().get("fixes", [])}
    return fixes, r.usd


def assemble(draft: dict[str, Any], *, brand: str, model: str, aliases: list[str], source_url: str,
             pages: list[Page], pdf_sha256: str | None, model_id: str, country: str,
             bad: dict[str, CitationResult], source: Source | None = None) -> tuple[dict[str, Any], list[Rejection]]:
    """The card from a checked draft. `bad` maps each failing citation's `where` to its result."""
    lang = draft["manual_lang"]
    rejected: list[Rejection] = []
    quote_ids: set[str] = set()
    texts = [p.text for p in pages]

    def new_qid(base: str) -> str:  # cut to 60 characters, two long ids can meet: the second is numbered
        return _unique(slug(base), quote_ids)

    def reject(where: str, obj: dict[str, Any], dropped: str) -> None:
        rejected.append(Rejection(where, int(obj["page"]), obj["quote"], bad[where].reason, dropped))

    def names(en: str, local: str) -> dict[str, str]:
        out = {"en": en.strip() or local.strip()}
        if lang != "en" and local.strip():
            out[lang] = local.strip()
        return out

    def q(obj: dict[str, Any]) -> dict[str, Any]:
        out = {"text": tidy_quote(obj["quote"]), "lang": lang, "page": int(obj["page"])}
        if lang != "en" and (en := (obj.get("quote_en") or "").strip()):
            out["en"] = en[:400]
        return out

    def part_name(en: str, local: str) -> dict[str, str]:
        en_name, local_name = kept_names(en, local)
        return names(en_name or local_name or one_name(en), local_name)  # a name in English is required

    red_zones = []
    for z in draft["red_zones"]:
        where = f"red_zones[{z['id']}]"
        if where in bad:
            reject(where, z, f"red zone {z['id']}")
            continue
        rule = zone_rule(z["rule"])
        if rule is None:
            continue
        red_zones.append({"id": z["id"], "part_id": z["part_id"], "rule_id": f"{country}.{rule}",
                          "page": int(z["page"]), "quote": q(z), "ring_cm": 5})
    zone_ids = {z["id"] for z in red_zones}
    gas_taps = {z["id"] for z in red_zones if z["rule_id"].endswith(".gas.tap")}

    # codes a passing fault row reads from a drawing (faultcodes): a job may name them too
    drawn_codes = frozenset(c for r in draft.get("troubleshooting", []) if f"troubleshooting[{r['id']}]" not in bad
                            for c in faultcodes.check(r["codes"], [p.text for p in pages], drawn_ok=True)[0]
                            if not faultcodes.printed(c, [p.text for p in pages]))
    quotes: list[dict[str, Any]] = []
    jobs = []
    refused: list[dict[str, Any]] = []  # contracts 1.23 card.refused: a name and a page, never steps
    for j in draft["jobs"]:
        jw = f"jobs[{j['id']}]"
        if draft["appliance_kind"] == "boiler" and j.get("task") not in BOILER_JOBS:
            page = int(j["steps"][0]["page"]) if j["steps"] else 0
            rejected.append(Rejection(jw, page, "", f"not_on_boiler_allow_list: task {j.get('task', 'other')}",
                                      f"job {j['id']} ({j['title_en']})"))
            if page:
                refused.append({"id": j["id"], "title": names(j["title_en"], j["title_manual"]),
                                "reason": refusal_reason(j, draft["red_zones"]), "page": page})
            continue
        for sid in j.get("_drafted_fixit", []):
            rejected.append(Rejection(f"{jw}.steps[{sid}]", 0, "", "drafted_fixit: only code adds our own step",
                                      f"step {sid}"))
        if j.get("_off_step"):
            for sid, page, clause, why in j["_off_step"]:
                rejected.append(Rejection(f"{jw}.steps[{sid}].done_when", page, clause, f"clause_off_step: {why}",
                                          f"job {j['id']}"))
            continue
        if j.get("_points_to_dropped"):  # a step sends the household to a page whose job is not on the card
            sid, page, target = j["_points_to_dropped"]
            rejected.append(Rejection(f"{jw}.steps[{sid}]", page, "", f"points_to_dropped_job: its quote sends to "
                                      f"page {target}, whose job was not built", f"job {j['id']} ({j['title_en']})"))
            refused.append({"id": j["id"], "title": names(j["title_en"], j["title_manual"]), "reason": "other",
                            "page": page})
            continue
        if j.get("_whole_job"):
            page = int(j["steps"][0]["page"]) if j["steps"] else 0
            rejected.append(Rejection(jw, page, "", "whole_job: " + "; ".join(map(str, j["_whole_job"])),
                                      f"job {j['id']} ({j['title_en']})"))
            continue
        bad_steps = [s for s in j["steps"] if f"{jw}.steps[{s['id']}]" in bad]
        if bad_steps or not j["steps"]:
            for s in bad_steps:
                reject(f"{jw}.steps[{s['id']}]", s, f"job {j['id']} (a step without a checked quote)")
            continue
        if z := zone_conflict(j, red_zones):  # the zone stays; a job on its part is not ours to guide
            page = int(j["steps"][0]["page"])
            rejected.append(Rejection(jw, page, "", f"zone_conflict: a step works on {z['part_id']}, under red zone "
                                      f"{z['id']} ({z['rule_id']})", f"job {j['id']} ({j['title_en']})"))
            refused.append({"id": j["id"], "title": names(j["title_en"], j["title_manual"]),
                            "reason": zone_reason(z), "page": page})
            continue
        if why := professional_only(j, texts):  # the manual's own words make it a professional's: never offered
            page = int(j["steps"][0]["page"])
            rejected.append(Rejection(jw, page, "", f"professional: {why}", f"job {j['id']} ({j['title_en']})"))
            refused.append({"id": j["id"], "title": names(j["title_en"], j["title_manual"]),
                            "reason": "needs_professional", "page": page})
            continue
        interval = None
        if j.get("interval"):
            if f"{jw}.interval" in bad:
                reject(f"{jw}.interval", j["interval"], f"the interval of job {j['id']}")
            elif int(j["interval"]["every"]) >= 1:
                qid = new_qid(f"q_{j['id']}_interval")
                quotes.append({"id": qid, **q(j["interval"])})
                interval = {"every": int(j["interval"]["every"]), "unit": j["interval"]["unit"], "quote_ref": qid}
        duration = None
        if j.get("duration"):
            if f"{jw}.duration" in bad:
                reject(f"{jw}.duration", j["duration"], f"the duration of job {j['id']}")
            elif float(j["duration"]["minutes"]) > 0:
                qid = new_qid(f"q_{j['id']}_duration")
                quotes.append({"id": qid, **q(j["duration"])})
                duration = {"value": float(j["duration"]["minutes"]), "source": "manual_quote", "ref": qid}
        steps: list[dict[str, Any]] = []
        for s in j["steps"]:
            if s.get("origin") == "fixit":
                steps.append({"id": s["id"], "text": names(s["text_en"], s["text_manual"]), "origin": "fixit",
                              "added_by": "ingest", "restores": s["restores"], "page": None, "quote": None,
                              "done_when": [], "parts": [], "power": "any", "zones": [],
                              "view": steps[-1]["view"] if steps else "front", "look_only_lines": []})
                continue
            zones = [z for z in s["zones"] if z in zone_ids]
            steps.append({
                "id": s["id"], "text": names(s["text_en"], s["text_manual"]), "page": int(s["page"]), "quote": q(s),
                "done_when": [{"id": d["id"], "text": {"en": d["text_en"]}, "quote_ref": None}
                              for d in s["done_when"] if d["text_en"].strip()],
                "parts": s["parts"], "power": s["power"], "zones": zones,
                "view": "side" if zones and s["view"] == "side" else s["view"],
                "look_only_lines": list(GAS_TAP_LOOK_ONLY) if gas_taps & set(zones) else [],
            })
        extra: dict[str, Any] = {}
        if j.get("due_name_en", "").strip():
            extra["due_name"] = names(j["due_name_en"], j.get("due_name_manual", ""))
        after = []
        for i, a in enumerate(j.get("after", [])):
            aw = f"{jw}.after[{i}]"
            if aw in bad:
                reject(aw, a, f"the 'after' link of job {j['id']}")
                continue
            qid = new_qid(f"q_{j['id']}_after_{i}")
            after.append(({"job_id": a["job_id"], "because": {"en": a["because_en"]}, "quote_ref": qid},
                          {"id": qid, **q(a)}))
        codes, not_codes = faultcodes.check(j["fault_codes"], [p.text for p in pages], drawn=drawn_codes)
        for code, why in not_codes:
            rejected.append(Rejection(f"jobs[{j['id']}].fault_codes", int(j["steps"][0]["page"]), "",
                                      f"fault_code: {why}", f"code {code!r} of job {j['id']}"))
        jobs.append({
            **extra, "_after": after,
            "id": j["id"], "title": names(j["title_en"], j["title_manual"]), "kind": j["kind"],
            "interval": interval if j["kind"] == "upkeep" else None,
            "fault_codes": codes,
            "landlord": bool(j["landlord"]), "duration_min": duration, "steps": steps,
        })

    jobs, dupes = one_job_per_title(jobs)
    gone = set()
    for d, kept in dupes:
        rejected.append(Rejection(f"jobs[{d['id']}]", int(d["steps"][0]["page"]), "",
                                  f"duplicate_job: the same title as job {kept}", f"job {d['id']}"))
        gone |= {r["quote_ref"] for r in (d["interval"],) if r} | {r["ref"] for r in (d["duration_min"],) if r}
    quotes = [q for q in quotes if q["id"] not in gone]
    kept_jobs = {j["id"] for j in jobs}
    witness_parts = {x for j in draft["jobs"] if j["id"] in kept_jobs for st in j["steps"] if st.get("witness")
                     for x in (st["witness"]["part"], st["witness"].get("anchor")) if x}
    job_ids = {j["id"] for j in jobs}
    for j in jobs:
        links = [(a, aq) for a, aq in j.pop("_after") if a["job_id"] in job_ids and a["job_id"] != j["id"]]
        if links:
            j["after"] = [a for a, _ in links]
            quotes.extend(aq for _, aq in links)
    used_parts = {p for j in jobs for s in j["steps"] for p in s["parts"]} | {z["part_id"] for z in red_zones}
    parts = []
    for p in draft["parts"]:
        if p["id"] not in used_parts | witness_parts or not p["sam_text"].strip():
            continue
        kind = "red_zone" if p["id"] in {z["part_id"] for z in red_zones} else "allowed"
        parts.append({"id": p["id"], "names": part_name(p["name_en"], p["name_manual"]), "locate": p["locate"],
                      "cue": p["cue"][:200], "sam_text": p["sam_text"][:40], "kind": kind})
    known = {p["id"] for p in parts}
    part_names = {p["id"]: p["names"]["en"] for p in parts}
    witnesses = {(j["id"], s["id"]): s.get("witness") for j in draft["jobs"] for s in j["steps"]}
    for j in jobs:
        for s in j["steps"]:
            s["parts"] = [x for x in s["parts"] if x in known]
            w = witnesses.get((j["id"], s["id"]))
            if w and w["part"] in known and _HAS_WITNESS:
                s["witness"] = {"part": w["part"], "expect": w["expect"],
                                "anchor": w["anchor"] if w.get("anchor") in known else None}
            if not s["done_when"] and s.get("witness"):
                s["done_when"] = [witness_clause(s["witness"], part_names)]
            if s["done_when"]:  # each clause cites the step's own checked quote
                qid = new_qid(f"q_{j['id']}_{s['id']}")
                quotes.append({"id": qid, **s["quote"]})
                for d in s["done_when"]:
                    d["quote_ref"] = qid
    red_zones = [z for z in red_zones if z["part_id"] in known]

    def text_or_none(en: str, local: str) -> dict[str, str] | None:
        return names(en, local) if (en.strip() or local.strip()) else None

    fix_jobs = {j["id"] for j in jobs if j["kind"] == "fix"}
    upkeep_jobs = {j["id"] for j in jobs if j["kind"] == "upkeep"}
    rows: list[dict[str, Any]] = []
    row_ids: set[str] = set()
    for r in draft.get("troubleshooting", []):
        where = f"troubleshooting[{r['id']}]"
        if where in bad:
            reject(where, r, f"troubleshooting row {r['id']}")
            continue
        codes, not_codes = faultcodes.check(r["codes"], [p.text for p in pages], drawn_ok=True)
        for code, why in not_codes:
            rejected.append(Rejection(f"{where}.codes", int(r["page"]), "", f"fault_code: {why}",
                                      f"code {code!r} of row {r['id']}"))
        indicator = text_or_none(r["indicator_en"], r["indicator_manual"])
        symptom = text_or_none(r["symptom_en"], r["symptom_manual"])
        meaning = text_or_none(r["meaning_en"], r["meaning_manual"])
        remedy = text_or_none(r["remedy_en"], r["remedy_manual"])
        why_not = ("no code, indicator or symptom" if not (codes or indicator or symptom)
                   else "no meaning or remedy" if not (meaning and remedy) else None)
        if why_not:
            rejected.append(Rejection(where, int(r["page"]), r["quote"], f"troubleshooting: {why_not}",
                                      f"troubleshooting row {r['id']}"))
            continue
        rid = r["id"] if r["id"] not in row_ids else slug(f"{r['id']}_{len(rows) + 1}")
        row_ids.add(rid)
        qid = new_qid(f"q_ts_{rid}")
        quotes.append({"id": qid, **q(r)})
        user = r["remedy_kind"] == "user"
        ties = []
        for i, t in enumerate(r["prevented_by"]):
            tw = f"{where}.prevented_by[{i}]"
            if tw in bad:
                reject(tw, t, f"the prevented_by tie of row {rid}")
            elif t["job_id"] in upkeep_jobs:
                tq = new_qid(f"q_ts_{rid}_prevented_{i}")
                quotes.append({"id": tq, **q(t)})
                ties.append({"job_id": t["job_id"], "quote_ref": tq})
        repeat = None
        if r.get("repeat"):
            rw = f"{where}.repeat"
            if rw in bad:
                reject(rw, r["repeat"], f"the repeat rule of row {rid}")
            elif int(r["repeat"]["count"]) >= 2 and int(r["repeat"]["days"]) >= 1:
                rq = new_qid(f"q_ts_{rid}_repeat")
                quotes.append({"id": rq, **q(r["repeat"])})
                repeat = {"count": int(r["repeat"]["count"]), "days": int(r["repeat"]["days"]), "quote_ref": rq}
        rows.append({
            "id": rid, "codes": codes, "indicator": indicator, "symbol": None, "symptom": symptom,
            "meaning": meaning, "cause": text_or_none(r["cause_en"], r["cause_manual"]), "remedy": remedy,
            "remedy_kind": r["remedy_kind"],
            "escalate_to": r["escalate_to"] if user and r["escalate_to"] != "none" else None,
            "job_ref": r["job_id"] if user and r["job_id"] in fix_jobs else None,
            "prevented_by": ties, "repeat": repeat, "page": int(r["page"]), "quote_ref": qid,
        })

    manual: dict[str, Any] = {"source_url": source_url, "lang": lang, "page_count": len(pages),
                              "page_text_sha256": [page_hash(p.text) for p in pages]}
    if draft.get("title"):
        manual["title"] = draft["title"]
    if draft.get("doc_id"):
        manual["doc_id"] = draft["doc_id"]
    if pdf_sha256:
        manual["pdf_sha256"] = pdf_sha256
    if source is not None:
        manual["source"] = source
    listed = models.checked(draft.get("models_listed", []), texts)
    models_page = models.listing_page(draft.get("models_listed", []), listed, texts)
    if models_page is None:
        listed = []  # every kept code is printed, so this is never the case; a code with no page never links
    if listed or source is not None:  # with a source, an empty list says the manual prints none
        manual["models"] = listed
    if listed:
        manual["models_page"] = models_page
    refused += technician_only(draft, texts, refused, lang)
    card = {
        "schema_version": 1, "brand": slug(brand), "model": model, "aliases": aliases,
        "appliance_kind": draft["appliance_kind"], "appliance_file_model": model_id, "manual": manual,
        "parts": parts, "red_zones": red_zones, "quotes": quotes, "jobs": jobs,
        **({"troubleshooting": rows} if rows else {}),
        **({"refused": refused} if refused else {}),
        "keywords": sorted({k.strip()[:40] for k in draft["keywords"] if k.strip()}
                           | {n.strip()[:40] for p in draft["parts"] if p["id"] in known
                              for n in re.split(_NAME_SEP, f"{p['name_en']} / {p['name_manual']}") if n.strip()}
                           | {n[:40] for p in parts if p["kind"] == "red_zone" for n in p["names"].values()}),
    }
    return card, rejected


def refusal_reason(job: dict[str, Any], zones: list[dict[str, Any]]) -> str:
    """Why code refused a boiler job, from the red zones its steps touch: gas, mains, or other (a job off the
    allow-list is not ours to guide; the manual has not said it is a professional's)."""
    rules = {z["id"]: zone_rule(z["rule"]) or "" for z in zones}
    touched = {rules.get(z, "") for s in job["steps"] for z in s["zones"]}
    if any(r.startswith("gas.") for r in touched):
        return "gas"
    if any(r.startswith("mains.") for r in touched):
        return "mains"
    return "other"


def zone_conflict(job: dict[str, Any], zones: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first red zone, other than heat, on a part one of the job's own steps works on: the card cannot both
    forbid the part and guide a hand to it. Heat is left to the heat lift; our restore step works on no part."""
    for z in zones:
        if ".heat." in f".{z['rule_id']}":
            continue
        if any(z["part_id"] in s.get("parts", []) for s in job["steps"] if s.get("origin") != "fixit"):
            return z
    return None


# the manual's own words for the people a job is handed to (read on norm()'d text: lower case, no hyphens)
_SERVICE = re.compile(
    r"\b(servicedienst|klantenservice|servicecentrum|service ?(centre|center|agent)|customer service|after sales"
    r"|authori[sz]ed (service|person|repairer|dealer)|technician|professional|qualified (person|electrician|installer)"
    r"|electrician|installateur|installer|vakman|monteur|deskundige|kundendienst|fachmann|fachkraft|techniker)")
_CONDITION = re.compile(r"\b(if|when|als|indien|wanneer|mocht|wenn|falls)\b")
_SUPPLY = re.compile(r"\b(available|obtain|order|verkrijgbaar|bestellen|erhältlich|bezugsquelle)\b")
_FUSE = re.compile(r"\b(fuses?|fuse box|circuit breakers?|zekering(en)?|meterkast|stoppen(kast)?|sicherung(en)?)\b")
_SECTION = re.compile(r"^(\d{1,2}(?:\.\d{1,2})+) [a-z]")


def _reserved_chapter(texts: list[str], page: int, quote: str) -> int | None:
    """The page of a sentence that hands the work of the chapter holding this quote to the service, in that chapter's
    own opening text (from its heading to its first numbered section), or None. A conditional handover ("if the code
    comes back, call the service") and a part to order from the service do not reserve the chapter."""
    if not 1 <= page <= len(texts) or (at := locate(texts[page - 1], quote)) is None:
        return None
    lines = [(n, ln) for n, t in enumerate(texts[:page], 1) for ln in norm(t, lines=True).split("\n")]
    text, start, _ = at
    here = len(lines) - len(text[start:].split("\n"))
    sec = next((m.group(1) for _, ln in reversed(lines[:here + 1]) if (m := _SECTION.match(ln))), None)
    if sec is None:
        return None
    parts = sec.split(".")
    for depth in range(1, len(parts)):
        head = re.compile(rf"^{re.escape('.'.join(parts[:depth]))} [a-z]")
        first = re.compile(rf"^{re.escape('.'.join(parts[:depth]))}\.1 [a-z]")  # its opening ends at its first section
        opens = next((i for i in range(here, -1, -1) if first.match(lines[i][1])), -1)
        top = next((i for i in range(opens, -1, -1) if head.match(lines[i][1])), None)
        if top is None or any(_SECTION.match(ln) for _, ln in lines[top + 1:opens]):
            continue  # no heading, or not this chapter's own (a figure's number, a list): nothing reserved
        for i in range(top + 1, opens):
            for sentence in re.split(r"(?<=[.!?])\s+", lines[i][1]):
                if _SERVICE.search(sentence) and not _CONDITION.search(sentence) and not _SUPPLY.search(sentence):
                    return lines[i][0]
    return None


def professional_only(job: dict[str, Any], texts: list[str]) -> str | None:
    """Why the manual itself makes this job a professional's, in fixed words, or None.

    - Most of its steps sit in a chapter whose own opening hands that chapter's work to the service (an
      installation chapter that says the installation is done by the authorised service).
    - A fault job whose only household action is at the house's fuses, on a fault row the manual hands to the
      service: what is left of the fault is the appliance's, and the service's."""
    own = [s for s in job["steps"] if s.get("origin") != "fixit" and s.get("quote")]
    held = [p for s in own if (p := _reserved_chapter(texts, int(s["page"]), s["quote"])) is not None]
    if own and 2 * len(held) > len(own):
        return f"the manual hands the chapter of its steps to the service (page {held[0]})"
    if job.get("kind") != "fix" or not own:
        return None
    rows = []
    for s in own:
        p = int(s["page"])
        at = locate(texts[p - 1], s["quote"]) if 1 <= p <= len(texts) else None
        text, start, end = at if at is not None else ("", 0, 0)
        rows.append((norm(s["quote"]), " ".join(re.split(r"(?<=[.!?])\s+", text[end:].replace("\n", " "))[:2])))
    handed = any(_SERVICE.search(q) or _SERVICE.search(after) for q, after in rows)
    at_fuse = [q for q, _ in rows if not _SERVICE.search(q)]
    if handed and at_fuse and all(_FUSE.search(q) for q in at_fuse):
        return "its only household action is at the house's fuses, and the manual hands the fault to the service"
    return None


_POINTER = re.compile(r"\b(?:blz|bladzijde|pagina|page|seite|s)\.?\s*(\d{1,3})\b")


def _title_words(s: str) -> set[str]:
    return {w for w in norm(s).replace(".", " ").split() if len(w) >= 4 and not w.isdigit()}


def points_to_dropped(draft: dict[str, Any], card: dict[str, Any],
                      pages: list[Page]) -> dict[str, tuple[str, int, int]]:
    """Each card job with a step whose quote sends the household to another page ("~ Blz. 38", "see page 12") for a
    job the card does not carry: {job id: (step id, its page, the page it points to)}. The job pointed at is the one
    on that page whose title and quotes share the most words with the pointing quote ("Zeef schoonmaken" -> "Zeef in de
    watertoevoer reinigen"), else every job with steps there. Such a step would point at nothing; its job is refused
    rather than any quote reworded."""
    kept = {j["id"] for j in card["jobs"]}
    by_label = {p.label: p.number for p in pages if p.label}
    words = {j["id"]: _title_words(" ".join([j.get("title_manual", ""), j.get("title_en", ""),
                                             *(st.get("quote") or "" for st in j["steps"])])) for j in draft["jobs"]}
    on: dict[int, set[str]] = {}
    for j in draft["jobs"]:
        for st in j["steps"]:
            if st.get("origin") != "fixit":
                on.setdefault(int(st["page"]), set()).add(j["id"])
    out: dict[str, tuple[str, int, int]] = {}
    for j in draft["jobs"]:
        if j["id"] not in kept:
            continue
        for st in j["steps"]:
            page = int(st.get("page") or 0)
            if st.get("origin") == "fixit" or not 1 <= page <= len(pages):
                continue
            at = locate(pages[page - 1].text, st["quote"])
            if at is None:
                continue
            text, start, end = at
            said = text[start:end].replace("\n", " ") + " " + text[end:end + 20].split("\n")[0]  # its own line only
            for m in _POINTER.finditer(said):
                target = by_label.get(m.group(1), int(m.group(1)))
                there = on.get(target, set()) - {j["id"]}
                shared = {k: len(words[k] & _title_words(said)) for k in there}
                named = {k for k in there if shared[k] and shared[k] == max(shared.values())}
                if target != page and (named or there) and not (named or there) & kept:
                    out[j["id"]] = (st["id"], page, target)
                    break
            if j["id"] in out:
                break
    return out


def zone_reason(zone: dict[str, Any]) -> str:
    """card.refused's reason for a job refused over a zone: gas or mains by the zone's rule, else other."""
    rule = zone["rule_id"].split(".", 1)[1] if "." in zone["rule_id"] else zone["rule_id"]
    return "gas" if rule.startswith("gas.") else "mains" if rule.startswith("mains.") else "other"


def technician_only(draft: dict[str, Any], pages: list[str], refused: list[dict[str, Any]],
                    lang: str) -> list[dict[str, Any]]:
    """The manual's own technician-only procedures, by the heading it prints (a heading it does not print is the
    draft's and is left out), for card.refused: who to call, never steps."""
    flat = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in pages]
    taken = {r["id"] for r in refused}
    out: list[dict[str, Any]] = []
    for u in draft.get("upkeep_procedures", []):
        head = re.sub(r"[^a-z0-9]", "", u["heading"].lower())
        if u.get("drop_reason") != "technician_only" or len(head) < 4:
            continue
        page = int(u["page"]) if 1 <= int(u["page"]) <= len(flat) and head in flat[int(u["page"]) - 1] else next(
            (n for n, t in enumerate(flat, 1) if head in t), 0)
        rid = slug(u["heading"])
        if page and rid not in taken:
            taken.add(rid)
            out.append({"id": rid, "title": {lang: u["heading"].strip()[:80]}, "reason": "needs_professional",
                        "page": page})
    return out


def coverage(draft: dict[str, Any], card: dict[str, Any], rejected: list[Rejection],
             pages: list[str]) -> list[dict[str, Any]]:
    """Each maintenance procedure the manual has, by its heading, and what became of it: `job` (on the card),
    `dropped` (a job code dropped, with the reason), `not_upkeep` (the draft's stated reason) or `missing` (neither:
    the main upkeep job class). A heading the manual does not print is the draft's, not the manual's: left out."""
    flat = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in pages]
    kept = {j["id"] for j in card.get("jobs", [])}
    why = {r.where.split("]")[0].removeprefix("jobs["): r.reason for r in rejected if r.where.startswith("jobs[")}
    out = []
    for u in draft.get("upkeep_procedures", []):
        head = re.sub(r"[^a-z0-9]", "", u["heading"].lower())
        if len(head) < 4 or not any(head in t for t in flat):
            continue
        page = int(u["page"]) if 1 <= int(u["page"]) <= len(flat) and head in flat[int(u["page"]) - 1] else next(
            n for n, t in enumerate(flat, 1) if head in t)
        jid = u.get("job_id") or ""
        if jid in kept:
            status, reason = "job", ""
        elif jid:
            status, reason = "dropped", why.get(jid, "not on the card")
        elif u.get("drop_reason"):
            status, reason = "not_upkeep", u["drop_reason"]
        else:
            status, reason = "missing", "no job and no reason"
        out.append({"heading": u["heading"][:120], "page": page, "job_id": jid or None, "status": status,
                    "reason": reason})
    return out


def checklist_coverage(draft: dict[str, Any], card: dict[str, Any], rejected: list[Rejection],
                       pages: list[str], lists: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """What became of each item of the appliance kind's checklist, by code: `job` (the manual gives it and a job
    on the card does it), `dropped` or `missing` (the manual gives it, with the heading as printed on its page, and
    no job on the card does it: a coverage gap; `weak` instead when only one manual the list came from holds the
    item, so it is asked about but never counted as a gap), `not_in_manual` (the reader found it nowhere: a line,
    never a job) or `unchecked` (the reader said the manual gives it but the heading is not on that page, or gave
    no answer).
    An item whose job is on the card counts as given, whatever the reader said."""
    items = (lists if lists is not None else checklists()).get(draft.get("appliance_kind", ""), [])
    flat = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in pages]
    kept = {j["id"] for j in card.get("jobs", [])}
    why = {r.where.split("]")[0].removeprefix("jobs["): r.reason for r in rejected if r.where.startswith("jobs[")}
    said = {c["item"]: c for c in draft.get("checklist", [])}
    out = []
    for i in items:
        c = said.get(i["id"], {})
        jid, page = c.get("job_id") or "", int(c.get("page") or 0)
        head = re.sub(r"[^a-z0-9]", "", (c.get("heading") or "").lower())
        printed = len(head) >= 4 and 1 <= page <= len(flat) and head in flat[page - 1]
        if jid in kept:
            status, reason = "job", ""
        elif c.get("in_manual") and printed:
            status, reason = ("dropped", why.get(jid, "not on the card")) if jid else ("missing", "no job")
            if i.get("evidence", 2) < 2:
                status, reason = "weak", f"{status}: {reason}"
        elif c and not c.get("in_manual"):
            status, reason = "not_in_manual", ""
        else:
            status, reason = "unchecked", "no answer" if not c else "heading not on its page"
        out.append({"item": i["id"], "name": i["names"]["en"], "heading": (c.get("heading") or "")[:120]
                    if printed else "", "page": page if printed else None, "job_id": jid or None,
                    "status": status, "reason": reason})
    return out


_UPKEEP_HEADING = re.compile(
    r"^\s*(?:\d{1,2}(?:\.\d{1,2})*\.?\s+)?(?:reiniging en onderhoud|onderhoud en reiniging|reinigen en onderhouden|"
    r"onderhoud|reiniging|schoonmaken|verzorging|cleaning and maintenance|care and cleaning|care and maintenance|"
    r"maintenance|cleaning|reinigen und pflegen|reinigung und pflege|pflege und reinigung|wartung|"
    r"entretien et nettoyage|nettoyage et entretien|entretien)\s*$", re.IGNORECASE | re.MULTILINE)


def maintenance_chapter(pages: list[str], keep: list[int] | None) -> int | None:
    """The first page, of those the draft read, that heads a maintenance chapter on a line of its own; None when
    the manual has none."""
    for n in keep or range(1, len(pages) + 1):
        if 1 <= n <= len(pages) and _UPKEEP_HEADING.search(pages[n - 1]):
            return n
    return None


def not_built(cov: list[dict[str, Any]], refused: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each of the manual's procedures the build could not turn into a job (dropped or missing), for card.refused
    as not_built: the manual's own heading and its page, so the app can say the manual also covers it there. One
    already refused by name (gas, mains, a professional's) is not listed twice."""
    named = {r["id"] for r in refused if "id" in r}
    out: list[dict[str, Any]] = []
    seen = set()
    for c in cov:
        head = " ".join(c["heading"].split())[:120]
        if c["status"] not in ("dropped", "missing") or c["job_id"] in named or not head or head.lower() in seen:
            continue
        seen.add(head.lower())
        out.append({"reason": "not_built", "heading": head, "page": c["page"]})
    return out


def one_job_per_title(jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """One job per task: a draft that repeats a job (same English title) keeps the copy with the most steps, at the
    place of the first. Returns (jobs, [(dropped job, the id kept)])."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for j in jobs:
        groups.setdefault(re.sub(r"[^a-z0-9]+", " ", j["title"]["en"].lower()).strip(), []).append(j)
    out, dropped = [], []
    for group in groups.values():  # in the order each title first appears
        keep = max(group, key=lambda j: len(j["steps"]))  # the first on a tie
        out.append(keep)
        dropped += [(j, keep["id"]) for j in group if j is not keep]
    return out, dropped


def rail_line(model_id: str, card: dict[str, Any] | None, rejected: list[Rejection]) -> str:
    name = model_id.split("-1")[0].removesuffix("-")
    if card is None:
        return f"{name}: manual → no card · kept as plain pages"
    steps = sum(len(j["steps"]) for j in card["jobs"])
    intervals = sum(1 for j in card["jobs"] if j["interval"])
    tail = "every one cited" if not rejected else f"{len(rejected)} rejected, the rest cited"
    return (f"{name}: manual → {steps} steps · {len(card['red_zones'])} red zones · "
            f"{intervals} interval{'s' if intervals != 1 else ''} · {tail}")


async def build(pdf: Path, *, brand: str, model: str, source_url: str, lane: str = "l3",
                aliases: list[str] | None = None, country: str = "nl",
                client: calls.Client | None = None, run_id: str | None = None,
                track: Tracker | None = None, household_lang: str | None = None,
                source: Source | None = None, household: str | None = None,
                household_cap_usd: float | None = None) -> IngestResult:
    """One ingest: the model drafts, code checks its quotes, then a text-only pass adds step clauses while one
    repair round runs, then the card or a failed ingest. Every call is tagged in the ledger as
    card-build:<brand>/<model>:<run_id> (a fresh id when none is given; an add passes its own, so the add and its
    build share one id). `track` collects the wait screen's fields (ingest.progress) as they become known.
    With `household`, every call is that household's: its ledger lines, so its request bodies are erased with it,
    and its daily cap."""
    with calls.run_tag(f"card-build:{brand}/{model}:{run_id or new_run_id()}"):
        return await _build(pdf, brand=brand, model=model, source_url=source_url, lane=lane, aliases=aliases,
                            country=country, client=client, track=track or Tracker(),
                            household_lang=household_lang, source=source,
                            who={"household": household, "household_cap_usd": household_cap_usd})


def new_run_id() -> str:
    """12 hex characters: one per add or build, the last field of its ledger run tag."""
    return uuid.uuid4().hex[:12]


def ready_job(j: dict[str, Any], lang: str, duration_ok: bool) -> dict[str, Any]:
    """A drafted job as the wait screen lists it (contracts 1.15, Ingest.jobs_ready): not startable until the whole
    card is checked. The title is model text: the server passes it through G0 before serving it."""
    title = {"en": j["title_en"].strip() or j["title_manual"].strip()}
    if lang != "en" and j["title_manual"].strip():
        title[lang] = j["title_manual"].strip()
    minutes = float(j["duration"]["minutes"]) if duration_ok and j.get("duration") else 0.0
    return {"id": j["id"], "title": title, "steps": len(j["steps"]), "page": int(j["steps"][0]["page"]),
            "duration_min": max(1, round(minutes)) if minutes > 0 else None, "kind": j["kind"], "startable": False}


def checked_jobs(draft: dict[str, Any], ok: dict[str, bool],
                 failing: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The drafted jobs whose every step quote has passed, as the card will keep them: a boiler's only from its
    allow-list, none that fails the whole-job check, one per title."""
    out: list[dict[str, Any]] = []
    titles: set[str] = set()
    for j in draft.get("jobs", []):
        jw = f"jobs[{j['id']}]"
        if not j.get("steps") or not all(ok.get(f"{jw}.steps[{s['id']}]") for s in j["steps"]
                                         if s.get("origin") != "fixit"):
            continue
        if draft.get("appliance_kind") == "boiler" and j.get("task") not in BOILER_JOBS:
            continue
        if j["id"] in failing or j.get("_whole_job"):
            continue
        key = re.sub(r"[^a-z0-9]+", " ", (j["title_en"] or j["title_manual"]).lower()).strip()
        if key in titles:
            continue
        titles.add(key)
        out.append(ready_job(j, draft.get("manual_lang", "en"), bool(ok.get(f"{jw}.duration"))))
    return out


def _live_jobs(track: Tracker, checker: _Checker, lang: str, *, brand: str, model: str,
               keep: list[int] | None = None) -> Callable[[str, int], None]:
    """The draft's on_delta: each job the model finishes writing is checked at once (its step quotes only), and
    listed on the wait screen when they all pass, while the draft goes on. The final check after the draft decides
    what the card keeps; a retried reply starts the list again."""
    stream = JobStream()
    ready: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {"checked": 0, "passed": 0}

    def on_delta(text: str, attempt: int) -> None:
        if attempt != stream.attempt:
            ready.clear()
            seen.clear()
            counts.update(checked=0, passed=0)
        jobs = stream.feed(text, attempt)
        for j in jobs:
            j["id"] = slug(str(j.get("id", "")))
            steps = j.get("steps") or []
            for st in steps:
                st["page"] = page_back(keep, int(st["page"]))
            oks = [checker.check(f"live[{j['id']}]", int(s["page"]), str(s["quote"])).ok for s in steps]
            counts["checked"] += len(oks)
            counts["passed"] += sum(oks)
            title = re.sub(r"[^a-z0-9]+", " ", str(j.get("title_en") or j.get("title_manual")).lower()).strip()
            kind = stream.top.get("appliance_kind", "other")
            if (not steps or not all(oks) or title in seen
                    or (kind == "boiler" and j.get("task") not in BOILER_JOBS)
                    or jobcheck.check(job_steps(j), checker.texts, kind)):
                continue
            seen.add(title)
            pick_title(j, None, brand=brand, model=model, lang=stream.top.get("manual_lang", lang))
            ready.append(ready_job(j, stream.top.get("manual_lang", lang), False))
        if jobs:
            share = min(1.0, len(ready) / 6)  # most cards have 4-10 jobs: the bar's draft share fills with them
            track.ready(list(ready), counts["checked"], counts["passed"],
                        DRAFT_FROM + (DRAFT_TO - DRAFT_FROM) * share * 0.9)

    return on_delta


async def _ticking(track: Tracker, every_s: float = 2.0) -> None:
    while True:
        await asyncio.sleep(every_s)
        track.drafting()


async def _build(pdf: Path, *, brand: str, model: str, source_url: str, lane: str, aliases: list[str] | None,
                 country: str, client: calls.Client | None, track: Tracker,
                 household_lang: str | None, source: Source | None, who: Who) -> IngestResult:
    cl = client or calls.default_client()
    model_id = cl.config.role("appliance_file").model
    pages = extract(pdf)
    texts = [p.text for p in pages]
    lang = track.manual(pdf, texts, household_lang)
    keep = language_pages(texts, lang)
    checker = _Checker(pages)
    usd = 0.0
    try:
        ticker = asyncio.create_task(_ticking(track))
        try:
            r = await cl.respond(
                "appliance_file", request_input(pdf_pages(pdf, keep) if keep else pdf.read_bytes(), pdf.name),
                instructions=instructions(), format=FILE_FORMAT, lane=lane, **who,
                on_delta=_live_jobs(track, checker, lang, brand=brand, model=model, keep=keep),
            )
        finally:
            ticker.cancel()
        usd += r.usd
        draft = r.parsed()
        # the draft read only the card's language pages: its pages back to the manual's
        for o in [o for _, o in _cites(draft)] + draft.get("models_listed", []) + draft.get("upkeep_procedures", []) \
                + draft.get("checklist", []):
            o["page"] = page_back(keep, int(o["page"]))
        _normalise_ids(draft)
        boiler_ids(draft)
        drafted_fixit(draft)
        drop_repeated_steps(draft)
        short_row_quotes(draft)
        for j in draft["jobs"]:
            if j.get("interval"):
                per_period(j["interval"])
        for j in draft["jobs"]:  # code's cleaning now; a proposed title replaces one still not a task, below
            pick_title(j, None, brand=brand, model=model, lang=draft["manual_lang"])
        checker = _Checker(pages)  # a fresh one: the live check's page corrections are not the card's
        # keyed by the cited object: the clause pass may rename a numbered step id, and with it the `where`
        by_obj = {id(o): checker.cite(w, o) for w, o in _cites(draft)}
        failed = [(w, o) for w, o in _cites(draft) if not by_obj[id(o)].ok]
        for _, o in _cites(draft):  # the pages the check found, so the gate below reads the text around them
            if by_obj[id(o)].ok and by_obj[id(o)].page is not None:
                o["page"] = by_obj[id(o)].page
        whole = whole_job_failures(draft, texts, gate=True, defects=False)  # what the gate will fail, repaired first
        mend = for_repair(draft, whole, texts)
        track.ready(checked_jobs(draft, {w: by_obj[id(o)].ok for w, o in _cites(draft)}, frozenset(whole)),
                    len(by_obj), len(by_obj) - len(failed), 0.8)

        async def repair() -> tuple[dict[str, tuple[int, str | None]], float]:
            return await _repair(failed, texts, lane=lane, client=cl, who=who) if failed else ({}, 0.0)

        async def job_repair() -> tuple[dict[str, dict[str, Any]], float]:
            return await _job_repair(draft, mend, texts, lane=lane, client=cl, who=who) if mend else ({}, 0.0)

        # the repairs read only what failed; their fixes land after the clause pass, which reads every quote
        clause_usd, (fixes, repair_usd), (job_fixes, job_usd), (alts, title_usd) = await asyncio.gather(
            _clauses(draft, lane=lane, client=cl, who=who, texts=texts), repair(), job_repair(),
            _titles(draft, brand=brand, model=model, lane=lane, client=cl, who=who))
        usd += clause_usd + repair_usd + job_usd + title_usd
        for j in draft["jobs"]:
            if j["id"] in alts:
                pick_title(j, alts[j["id"]], brand=brand, model=model, lang=draft["manual_lang"])
        def take(fixed: dict[str, tuple[int, str | None]], tried_on: list[tuple[str, dict[str, Any]]]) -> None:
            for w, o in tried_on:  # a fixed quote is taken only when it passes the same check
                page, quote = fixed.get(w, (o["page"], None))
                if quote:
                    tried = {"page": page, "quote": quote, "quote_en": o.get("quote_en", "")}
                    retry = checker.cite(w, tried)
                    if retry.ok:
                        o["page"], o["quote"], o["quote_en"] = page, tried["quote"], tried["quote_en"]
                        by_obj[id(o)] = retry

        take(fixes, failed)
        repaired = [j for j in draft["jobs"] if j["id"] in job_fixes and apply_job_fix(j, job_fixes[j["id"]])]
        if repaired:  # the added steps get their clauses, ids and citation check like any other
            usd += await _clauses({"jobs": repaired, "parts": draft.get("parts", [])}, lane=lane, client=cl,
                                   who=who, texts=texts)
            _normalise_ids(draft)
            added = [(w, o) for w, o in _cites(draft) if id(o) not in by_obj]
            for w, o in added:
                by_obj[id(o)] = checker.cite(w, o)
            if still := [(w, o) for w, o in added if not by_obj[id(o)].ok]:  # the same quote repair, once
                try:
                    fixes, repair_usd = await _repair(still, texts, lane=lane, client=cl, who=who)
                except calls.CallError:
                    fixes, repair_usd = {}, 0.0
                usd += repair_usd
                take(fixes, still)
        results = {w: by_obj[id(o)] for w, o in _cites(draft)}
        for w, o in _cites(draft):  # the pages the check found, before the final say reads the text around them
            if results[w].ok and results[w].page is not None:
                o["page"] = results[w].page
        final = whole_job_failures(draft, texts, gate=True)
        if append_restores(draft, final, texts):  # our fixed restore step, then the check once more
            final = whole_job_failures(draft, texts, gate=True)
            results = {w: by_obj[id(o)] for w, o in _cites(draft)}
        for jid, fails in final.items():  # the final say, after every repair
            next(j for j in draft["jobs"] if j["id"] == jid)["_whole_job"] = fails
        bad = {w: res for w, res in results.items() if not res.ok}
        usd += await _translate(draft, bad, lane=lane, client=cl, who=who)
        card, rejected = assemble(draft, brand=brand, model=model, aliases=aliases or [], source_url=source_url,
                                  pages=pages, pdf_sha256=file_sha256(pdf), model_id=model_id, country=country,
                                  bad=bad, source=source)
        if pointing := points_to_dropped(draft, card, pages):  # a step pointing at nothing: its job goes too
            for j in draft["jobs"]:
                if j["id"] in pointing:
                    j["_points_to_dropped"] = pointing[j["id"]]
            card, rejected = assemble(draft, brand=brand, model=model, aliases=aliases or [], source_url=source_url,
                                      pages=pages, pdf_sha256=file_sha256(pdf), model_id=model_id, country=country,
                                      bad=bad, source=source)
        if not card["jobs"]:
            raise ValueError("no job survived the citation check")
        if twice := card_duplicate_ids(card):
            raise ValueError("; ".join(twice))
        if card.get("troubleshooting"):  # where each indicator's symbol is drawn: code, from the PDF itself
            found = symbols.locate(pdf, card["troubleshooting"], {q["id"]: q["text"] for q in card["quotes"]})
            for r in card["troubleshooting"]:
                r["symbol"] = found.get(r["id"])
        lifted = heat_lifts(card) if HEAT_LIFT else []
        lift_heat(card, lifted)
        cov = coverage(draft, card, rejected, texts)
        if not cov and (chapter := maintenance_chapter(texts, keep)):  # a draft that lists nothing hides every gap
            raise ValueError(f"the manual has a maintenance chapter (page {chapter}) and the draft listed none of "
                             "its procedures")
        asked = checklist_coverage(draft, card, rejected, texts)
        if left := not_built(cov + asked, card.get("refused", [])):
            card["refused"] = [*card.get("refused", []), *left]
        jsonschema.validate(card, CARD_SCHEMA)
    except (calls.CallError, ValueError, KeyError, TypeError, jsonschema.ValidationError) as e:
        return IngestResult(None, rail_line(model_id, None, []), usd=usd, model=model_id,
                            error=f"{type(e).__name__}: {str(e)[:300]}",
                            cap_reached=isinstance(e, calls.BudgetExceeded))
    kept = {j["id"] for j in card["jobs"]}
    passed = sum(1 for res in results.values() if res.ok)
    track.ready([j for j in checked_jobs(draft, {w: res.ok for w, res in results.items()}) if j["id"] in kept],
                len(results), passed, 0.95)
    return IngestResult(card, rail_line(model_id, card, rejected), rejected, checker.corrected, usd, model_id,
                        models=card["manual"].get("models", []),
                        coverage=cov, lifted=lifted, checklist=asked)
