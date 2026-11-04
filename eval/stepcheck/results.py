"""Step-check predictions: eval/results/stepcheck/<variant>.jsonl, one line per (clip, run).

These are the recorded judge outcomes the published table is computed from, so `make eval` reproduces the table on
a clean clone with no frames and no key. `python -m eval.stepcheck run` regenerates them from the clips: in replay
from fixtures/l6/, or live in record mode through calls/ and the ledger.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parent.parent
RESULTS = EVAL / "results" / "stepcheck"
LABELS = EVAL / "labels" / "stepcheck.jsonl"


@dataclass(frozen=True)
class ClipSet:
    """A labelled set of step-check clips, scored as its own row: its plan, labels, results and claim prefix."""

    name: str
    prefix: str  # claim ids start "<prefix>.<variant>."
    about: str

    @property
    def plan(self) -> Path:
        return EVAL / "labels" / f"{self.prefix}.plan.jsonl"

    @property
    def labels(self) -> Path:
        return EVAL / "labels" / f"{self.prefix}.jsonl"

    @property
    def results(self) -> Path:
        return EVAL / "results" / ("stepcheck" if self.name == "main" else f"stepcheck-{self.name}")


SETS = {s.name: s for s in (
    ClipSet("main", "stepcheck", "the 120 pre-registered clips: 20 steps x done / not-done / can't-see x 2 takes"),
    ClipSet("heldout", "heldout", "20 clips from two neighbours' appliances of other brands"),
    ClipSet("power", "power", "no-part power steps (unplug): the judge grounded on the plug and socket, plug-out as "
                              "the second witness, as in Session.check"),
)}


@dataclass(frozen=True)
class ClauseFinding:
    clause_id: str
    visible: bool
    state: str
    evidence: str


@dataclass(frozen=True)
class Prediction:
    clip_id: str
    variant: str
    run: int
    result: str  # done | not_done | cant_see: the verdict code computed
    judge_result: str | None = None  # the judge's own verdict before the witness
    sam_result: str | None = None
    clauses: list[ClauseFinding] = field(default_factory=list)
    ask_for: str | None = None
    latency_ms: float | None = None
    usd: float | None = None
    error: str | None = None

    @staticmethod
    def from_json(d: dict[str, Any]) -> Prediction:
        return Prediction(**{**d, "clauses": [ClauseFinding(**c) for c in d.get("clauses", [])]})


@dataclass(frozen=True)
class Label:
    clip_id: str
    step_id: str
    appliance: str
    truth: str
    take: int
    guarded: bool


def load_labels(path: Path = LABELS) -> dict[str, Label]:
    out: dict[str, Label] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            out[d["clip_id"]] = Label(d["clip_id"], d["step_id"], d["appliance"], d["truth"], int(d["take"]),
                                      bool(d["guarded"]))
    return out


def load_predictions(variant: str, root: Path = RESULTS) -> list[Prediction]:
    path = root / f"{variant}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no recorded predictions for variant {variant} at {path}")
    return [Prediction.from_json(json.loads(x)) for x in path.read_text().splitlines() if x.strip()]


def write_predictions(variant: str, preds: list[Prediction], root: Path = RESULTS) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{variant}.jsonl"
    preds = sorted(preds, key=lambda p: (p.run, p.clip_id))
    path.write_text("".join(json.dumps(asdict(p), ensure_ascii=False) + "\n" for p in preds))
    return path
