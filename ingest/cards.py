"""Cards on disk: cards/<brand>/<model>.yaml, schema-checked on write and on read."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from calls.config import ROOT

CARDS = ROOT / "cards"
CARD_SCHEMA = json.loads((ROOT / "contracts" / "card.schema.json").read_text())


def card_filename(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model).strip("_") + ".yaml"


def card_path(brand: str, model: str, root: Path = CARDS) -> Path:
    return root / brand / card_filename(model)


def write_card(card: dict[str, Any], root: Path = CARDS) -> Path:
    jsonschema.validate(card, CARD_SCHEMA)
    path = card_path(card["brand"], card["model"], root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(card, allow_unicode=True, sort_keys=False, width=120))
    return path


def read_card(path: Path) -> dict[str, Any]:
    card: dict[str, Any] = yaml.safe_load(path.read_text())
    jsonschema.validate(card, CARD_SCHEMA)
    return card


def all_cards(root: Path = CARDS) -> list[Path]:
    return sorted(root.glob("*/*.yaml"))
