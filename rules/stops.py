"""The line the phone leads a stop with, per stop script: `python -m rules.stops [--check]`.

A stop script is several sentences, which is long to read with gas in the air. The phone shows the rule's own short
line first, the call buttons under it, and the full script below them. The phone gets the script itself in the Stop
message, so this table maps each script `stop_script` can return to the line of the same rule and language. No text
here is new: both come from the country files. Only a script that names a number to call has a lead (the lead is there
to lift the call buttons); any other is shown as before, alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rules.country import load_country, shipped
from rules.engine import stop_script
from rules.numbers import NUMBERS

HERE = Path(__file__).resolve().parent
JSON_PATH = HERE / "stops.json"


def leads() -> dict[str, str]:
    """Each STOP rule's script that names one of the country's numbers → its line in the same language; the English
    one also stands for the fallback (English plus the emergency advice) in any other language."""
    out: dict[str, str] = {}
    for code in shipped():
        c = load_country(code)
        if c is None:
            continue
        for r in c.rules.rules:
            if r.action != "STOP":
                continue
            lines, scripts = r.line.root, r.script.root
            calls = {lang for lang, text in scripts.items() if any(n in text for n in NUMBERS.get(code, []))}
            out.update((stop_script(c, r.id, lang), lines[lang]) for lang in calls if lang in lines)
            if "en" in calls and "en" in lines:  # no script in the answer language: English plus the emergency advice
                out[stop_script(c, r.id, "xx")] = lines["en"]
    return out


def as_json() -> str:
    return json.dumps(dict(sorted(leads().items())), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """`python -m rules.stops` writes stops.json; `--check` fails if it is stale."""
    args = sys.argv[1:] if argv is None else argv
    if args == ["--check"]:
        if not JSON_PATH.exists() or JSON_PATH.read_text() != as_json():
            print("rules/stops.json is stale: run python -m rules.stops", file=sys.stderr)
            return 1
        return 0
    JSON_PATH.write_text(as_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
