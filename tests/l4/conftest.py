"""Test cards for the rules: the fake Acme washer (the contracts' example) and a fake boiler built from it."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from contracts.models.card import Card

ROOT = Path(__file__).resolve().parents[2]
ACME = json.loads((ROOT / "contracts/examples/card/acme-wm-0000.json").read_text())
YELLOW_EN = "The yellow tap is gas. Check it's open: just look."
YELLOW_NL = "De gele kraan is gas. Kijk of hij open staat: alleen kijken."


def acme_data() -> dict[str, Any]:
    data = copy.deepcopy(ACME)
    for z in data["red_zones"]:
        z["rule_id"] = "nl.mains.casing"
    return data


def boiler_data() -> dict[str, Any]:
    step = {
        "page": 6, "quote": {"text": "Open de vul- en aftapkraan een kwartslag.", "lang": "nl", "page": 6},
        "done_when": [], "power": "any", "view": "side", "look_only_lines": [],
    }
    return {
        "schema_version": 1, "brand": "acme", "model": "BX-0000", "aliases": [], "appliance_kind": "boiler",
        "appliance_file_model": "muse-wonder-1.0",
        "manual": {"source_url": "https://example.invalid/acme/bx-0000", "lang": "nl", "page_count": 8,
                   "page_text_sha256": []},
        "parts": [
            {"id": "fill_tap", "names": {"en": "fill and drain tap", "nl": "vul- en aftapkraan"},
             "locate": "appearance", "cue": "small tap with a cap under the boiler", "sam_text": "small tap",
             "kind": "allowed"},
            {"id": "gas_tap", "names": {"en": "gas tap", "nl": "gaskraan"}, "locate": "function",
             "cue": "yellow handle on the gas pipe", "sam_text": "yellow tap handle", "kind": "red_zone"},
        ],
        "red_zones": [{"id": "gas", "part_id": "gas_tap", "rule_id": "nl.gas.tap", "page": 4, "ring_cm": 20,
                       "quote": {"text": "Laat de gaskraan altijd met rust.", "lang": "nl", "page": 4}}],
        "quotes": [],
        "jobs": [
            {"id": "top_up", "title": {"en": "Top up the pressure"}, "kind": "upkeep", "interval": None,
             "fault_codes": [], "landlord": False, "duration_min": None, "steps": [
                 {**step, "id": "check_gas_open", "text": {"en": YELLOW_EN, "nl": YELLOW_NL}, "parts": [],
                  "zones": ["gas"], "look_only_lines": [YELLOW_EN, YELLOW_NL]},
                 {**step, "id": "open_fill_tap", "text": {"en": "Open the fill and drain tap a quarter turn."},
                  "parts": ["fill_tap"], "zones": ["gas"], "zone_rings": [{"zone_id": "gas", "ring_cm": 3}]},
             ]},
            {"id": "clean_burner", "title": {"en": "Clean it"}, "kind": "fix", "interval": None, "fault_codes": [],
             "landlord": False, "duration_min": None, "steps": [
                 {**step, "id": "wipe", "text": {"en": "Wipe the inside."}, "parts": [], "zones": []}]},
        ],
        "keywords": [],
    }


@pytest.fixture
def acme() -> Card:
    return Card.model_validate(acme_data())


@pytest.fixture
def boiler() -> Card:
    return Card.model_validate(boiler_data())
