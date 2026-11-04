"""The disclaimer and AI-disclosure lines (`rules/disclaimer.yaml`): fixed text, English where a language is missing."""

from __future__ import annotations

import json
import sys
from functools import cache
from pathlib import Path
from typing import Literal

import yaml

Kind = Literal["first_run", "spoken", "step_citation", "held_follow_on", "terms"]
PATH = Path(__file__).resolve().parent / "disclaimer.yaml"


@cache
def _texts() -> dict[str, dict[str, str]]:
    data = yaml.safe_load(PATH.read_text())
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def disclaimer(kind: Kind, lang: str, *, page: int | None = None) -> str:
    texts = _texts()[kind]
    text = texts.get(lang) or texts["en"]
    if kind == "step_citation":
        if page is None:
            raise ValueError("a step citation needs the page from the card")
        text = text.format(page=page)
    return text


JSON_PATH = PATH.with_suffix(".json")


def as_json() -> str:
    """disclaimer.json for the phone, which has no YAML parser: the same data, generated from the YAML."""
    return json.dumps(yaml.safe_load(PATH.read_text()), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """`python -m rules.disclaimer` writes disclaimer.json; `--check` fails if it is stale."""
    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        if not JSON_PATH.exists() or JSON_PATH.read_text() != as_json():
            print(f"{JSON_PATH.name} is stale: run python -m rules.disclaimer", file=sys.stderr)
            return 1
        return 0
    JSON_PATH.write_text(as_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
