"""The phone numbers the stop and hold texts tell the user to call, per country: `python -m rules.numbers [--check]`.

The phone keeps each one on one line and makes it a call button (polish finding 7: "0800-" broke from "9009").
It matches the display string exactly, so the texts and this table must agree; tests/l4/test_numbers.py checks that
every number in a country's texts is listed here and every listed number is in its texts.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from rules.country import load_country, shipped

HERE = Path(__file__).resolve().parent
JSON_PATH = HERE / "numbers.json"

# As written in the country files. Longest first, so the phone matches "0800 111 999" before "999".
NUMBERS: dict[str, list[str]] = {
    "nl": ["0800-9009", "112"],
    "uk": ["0800 111 999", "0800 002 001", "999", "105"],
    "us": ["911"],
    "de": ["112"],
}
# Digits in the texts that are not numbers to call.
NOT_NUMBERS = frozenset({"608"})  # US: "EPA 608 certified"

_NUM = re.compile(r"(?<![\w.])\d[\d -]*\d(?!\w)")


def texts(code: str) -> list[str]:
    """Every text of a country that the user reads or hears: emergency advice, rule lines and stop scripts."""
    c = load_country(code)
    if c is None:
        return []
    out = list(c.rules.emergency.root.values())
    for r in c.rules.rules:
        out += list(r.line.root.values()) + list(r.script.root.values())
    return out


def found(code: str) -> set[str]:
    return {m.group().strip() for t in texts(code) for m in _NUM.finditer(t)} - NOT_NUMBERS


def as_json() -> str:
    data = {cc: [{"display": n, "tel": re.sub(r"\D", "", n)} for n in NUMBERS.get(cc, [])] for cc in shipped()}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """`python -m rules.numbers` writes numbers.json; `--check` fails if it is stale."""
    args = sys.argv[1:] if argv is None else argv
    if args == ["--check"]:
        if not JSON_PATH.exists() or JSON_PATH.read_text() != as_json():
            print("rules/numbers.json is stale: run python -m rules.numbers", file=sys.stderr)
            return 1
        return 0
    JSON_PATH.write_text(as_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
