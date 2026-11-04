"""Which steps a stop or hold guards, from the card, by the fixit-rules CLI (eval never imports rules/).

`python -m rules guarded --card <path>` prints {step_id: bool} for the whole card. Until an eval
step names its card and card step in eval/steps.toml, its interim hand-set `guarded` flag stands.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
Runner = Callable[[list[str]], str]


def _run(argv: list[str]) -> str:
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def card_guarded(card: str, run: Runner = _run) -> dict[str, bool]:
    out = json.loads(run([sys.executable, "-m", "rules", "guarded", "--card", card]))
    if not isinstance(out, dict) or not all(isinstance(v, bool) for v in out.values()):
        raise ValueError(f"fixit-rules guarded --card {card} printed {out!r}, not a step map")
    return out


def resolve(steps: list[dict[str, object]], run: Runner = _run) -> dict[str, bool]:
    """eval step id -> guarded: from the card where the step names one, else the step's own interim flag."""
    cache: dict[str, dict[str, bool]] = {}
    out = {}
    for s in steps:
        card, card_step = s.get("card"), s.get("card_step")
        if isinstance(card, str) and isinstance(card_step, str):
            if card not in cache:
                cache[card] = card_guarded(card, run)
            if card_step not in cache[card]:
                raise KeyError(f"{s['id']}: {card} has no step {card_step}")
            out[str(s["id"])] = cache[card][card_step]
        else:
            out[str(s["id"])] = bool(s["guarded"])
    return out
