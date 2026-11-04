"""The phone's own rows: does the gas tap and the washer's casing stop a reach, does a near pass or a fill-valve turn
stop it by mistake, how long from the frame that fired to STOP on the screen, and the phone's own distance at each
stop for the slider-camera gap rows (eval.gap).

    python -m eval.phone read [--sessions DIR] [--runs DIR]   # build machine: session records -> readings
    python -m eval.phone stops                                # phone.gas.*, phone.casing.*: from the readings
    python -m eval.phone stop-ms                              # phone.stop_p95_ms: from the readings
    python -m eval.phone gap [--sessions DIR]                 # fills phone_gap_cm in eval/labels/gap.jsonl

Trials are eval/labels/phone_stops.jsonl, one line each:
    {"trial_id": "gas.r07", "kind": "reach", "session_id": "...", "from_ms": 41200, "to_ms": 44800}
kind is reach, near_pass or fill_valve (the boiler's gas tap), covers (a hand over the tap's outline), or
casing_reach (a screwdriver to the back panel of the idle washer, plugged in, no programme started). from_ms and to_ms
are on the session record's clock: the t_ms of the phone's own c2s messages in events.jsonl, which is the phone's
clock. A trial "stopped" when a c2s g1 event of kind stop falls in [from_ms, to_ms] and fired on that trial's part:
a zone whose rule is the gas tap (<region>.gas.tap) for the four gas kinds, the casing (<region>.mains.casing) for
casing_reach. A g1 event names its zone_id; the zone's rule is the latest s2c zone with that zone_id before it in the
record. For reach, covers and casing_reach a stop is the wanted outcome; for near_pass and fill_valve it is a false
stop. Each denominator is the number of label lines of that kind, never a typed count.

The session records (FIXIT_PRIVATE/sessions/<id>/events.jsonl) and the phone's saved timings
(FIXIT_PRIVATE/runs/phone/<session_id>.json, from "Save the timings" with ?debug=1) hold household data and stay
private. `read` turns them into readings that hold only ids, kinds and numbers (eval/results/phone/*.jsonl), and
`stops` and `stop-ms` print claims from those, so `make eval` reproduces them without the private folder. Both
refuse readings whose trials no longer match the labels, so a relabel never keeps old numbers. stop-ms counts only
the stops that ended a labelled trial (by session and frame), never a rehearsal or setup stop in the same file.

frame -> STOP is measured on the phone: from the capture of the frame whose G1 fired to the first paint that shows
the red screen. The display's next refresh comes after that, so it is up to one refresh short of light on glass.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from eval.stats import percentile, wilson_count

EVAL = Path(__file__).resolve().parent
TRIALS = EVAL / "labels" / "phone_stops.jsonl"
GAP_STOPS = EVAL / "labels" / "gap.jsonl"
RESULTS = EVAL / "results" / "phone"
STOPS_READ = RESULTS / "stops.jsonl"
TIMINGS_READ = RESULTS / "timings.jsonl"

KINDS = ("reach", "near_pass", "fill_valve", "covers", "casing_reach")
# The part each kind of trial is about: a zone's rule_id without its region ("nl.gas.tap" -> "gas.tap").
PART = {
    "reach": "gas.tap",
    "near_pass": "gas.tap",
    "fill_valve": "gas.tap",
    "covers": "gas.tap",
    "casing_reach": "mains.casing",
}
# Claim id, the trial kind it counts, and its unit.
STOP_CLAIMS = (
    ("phone.gas.reach_stopped", "reach", "reaches toward the gas tap of n that stopped"),
    ("phone.gas.near_pass_false_stops", "near_pass", "near passes of n that stopped (false stops)"),
    ("phone.gas.fill_valve_false_stops", "fill_valve", "fill-valve operations of n that stopped (false stops)"),
    ("phone.gas.covers_stopped", "covers", "hands over the gas tap's outline of n that stopped"),
    ("phone.casing.reach_stopped", "casing_reach", "screwdriver reaches to the idle washer's casing of n that stopped"),
)
STOP_MS_UNIT = "ms from the frame that fired to the red screen painted, p95 over n stops on the phone"


def phone_gap_cm(g1: dict[str, Any], zone: dict[str, Any]) -> float:
    """The phone's distance to the part itself at a STOP: the g1 event's dist_cm (to the ring's edge) plus the ring,
    never under 3 cm, plus the tool length the zone grew the ring by. A covered stop with no dist_cm is 0 to the ring.
    """
    dist = g1.get("dist_cm")
    ring = max(float(zone.get("ring_cm") or 0.0), 3.0)
    tool = float(zone.get("tool_len_cm") or 0.0)
    return round((0.0 if dist is None else float(dist)) + ring + tool, 2)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Keys stay in each line's own order, so filling one field shows as that change alone in a diff."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def events(sessions: Path, session_id: str) -> list[dict[str, Any]]:
    """One session record's lines, in the order they were written."""
    path = sessions / session_id / "events.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no session record for trial session {session_id!r}")
    return read_jsonl(path)


def part(rule_id: Any) -> str | None:
    """A zone's rule without its region: "nl.gas.tap" -> "gas.tap"."""
    return str(rule_id).split(".", 1)[1] if rule_id and "." in str(rule_id) else None


def g1_stops(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The record's c2s g1 events of kind stop, each with the record's t_ms and the part its zone is on (None when
    no zone with its zone_id came before it)."""
    zones: dict[str, Any] = {}
    out = []
    for e in lines:
        m = e.get("msg") or {}
        if e.get("dir") == "s2c" and m.get("type") == "zone":
            zones[str(m.get("zone_id"))] = m.get("rule_id")
        elif e.get("dir") == "c2s" and m.get("type") == "g1" and m.get("kind") == "stop":
            out.append({**m, "t_ms": e.get("t_ms", m.get("t_ms")), "part": part(zones.get(str(m.get("zone_id"))))})
    return out


def zone_for(lines: list[dict[str, Any]], frame_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The g1 stop event for a frame and the zone it fired on: the latest s2c zone with its zone_id before it in the
    record (records keep arrival order; the server's and the phone's clocks are not compared)."""
    zones: dict[str, dict[str, Any]] = {}
    for e in lines:
        m = e.get("msg") or {}
        if e.get("dir") == "s2c" and m.get("type") == "zone":
            zones[str(m.get("zone_id"))] = m
        elif (
            e.get("dir") == "c2s"
            and m.get("type") == "g1"
            and m.get("kind") == "stop"
            and m.get("frame_id") == frame_id
        ):
            z = zones.get(str(m.get("zone_id")))
            return (m, z) if z is not None else None
    return None


def read_trials(trials: list[dict[str, Any]], sessions: Path) -> list[dict[str, Any]]:
    """Each trial's outcome: whether a STOP on its part fell in its window, and the first one's frame and time into
    the window."""
    cache: dict[str, list[dict[str, Any]]] = {}
    out = []
    for t in trials:
        if t.get("kind") not in KINDS:
            raise ValueError(f"trial {t.get('trial_id')!r}: kind {t.get('kind')!r} is not one of {', '.join(KINDS)}")
        sid = str(t["session_id"])
        lines = cache.setdefault(sid, events(sessions, sid))
        lo, hi = float(t["from_ms"]), float(t["to_ms"])
        hits = [s for s in g1_stops(lines) if lo <= float(s["t_ms"]) <= hi and s["part"] == PART[t["kind"]]]
        first = min(hits, key=lambda s: float(s["t_ms"])) if hits else None
        out.append(
            {
                "trial_id": t["trial_id"],
                "kind": t["kind"],
                "session_id": sid,
                "stopped": first is not None,
                "frame_id": first["frame_id"] if first else None,
                "reason": first.get("reason") if first else None,
                "after_ms": round(float(first["t_ms"]) - lo, 1) if first else None,
            }
        )
    return out


def read_timings(runs: Path) -> list[dict[str, Any]]:
    """The phone's saved timing files as readings: session, each stop's frame and ms. Nothing else is kept."""
    out = []
    for path in sorted(runs.glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        frames = run.get("stop_frames") or []
        out.append(
            {
                "session_id": run.get("session_id"),
                "at": run.get("at"),
                "stops": [{"frame_id": str(f["frame_id"]), "ms": float(f["ms"])} for f in frames],
            }
        )
    return out


def stop_claims(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for cid, kind, unit in STOP_CLAIMS:
        rs = [r for r in readings if r["kind"] == kind]
        n, k = len(rs), sum(1 for r in rs if r["stopped"])
        out.append(
            {"id": cid, "value": k if n else None, "unit": unit, "n": n, "ci": list(wilson_count(k, n)) if n else None}
        )
    return out


def stale(readings: list[dict[str, Any]], trials: list[dict[str, Any]]) -> bool:
    """Whether the readings were read from other labels than the ones now in the repo."""

    def key(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
        return sorted((str(r.get("trial_id")), str(r.get("kind"))) for r in rows)

    return key(readings) != key(trials)


def stop_ms_claims(timings: list[dict[str, Any]], readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """p95 over the stops that ended a labelled trial: the first stop on its part in the window, by session and
    frame."""
    ended = {(r["session_id"], r["frame_id"]) for r in readings if r["stopped"]}
    ms = [s["ms"] for run in timings for s in run["stops"] if (run["session_id"], s["frame_id"]) in ended]
    return [
        {
            "id": "phone.stop_p95_ms",
            "value": round(percentile(ms, 95)) if ms else None,
            "unit": STOP_MS_UNIT,
            "n": len(ms),
            "ci": None,
        }
    ]


def fill_gap(stops: list[dict[str, Any]], sessions: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """gap.jsonl lines with phone_gap_cm set from the record, for each line that names its session_id and frame_id.
    The second value lists the stop ids whose frame has no g1 stop (or no zone) in that record."""
    cache: dict[str, list[dict[str, Any]]] = {}
    out, missing = [], []
    for s in stops:
        sid, fid = s.get("session_id"), s.get("frame_id")
        if not sid or not fid:
            out.append(s)
            continue
        lines = cache.setdefault(str(sid), events(sessions, str(sid)))
        found = zone_for(lines, str(fid))
        if found is None:
            missing.append(str(s.get("stop_id")))
            out.append(s)
            continue
        out.append({**s, "phone_gap_cm": phone_gap_cm(*found)})
    return out, missing


def private_root(sub: str) -> Path | None:
    from calls import private_dir

    priv = private_dir()
    return None if priv is None else priv / sub


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.phone")
    ap.add_argument("what", choices=["read", "stops", "stop-ms", "gap"])
    ap.add_argument("--sessions", type=Path)
    ap.add_argument("--runs", type=Path)
    a = ap.parse_args(argv)
    if a.what in ("stops", "stop-ms"):
        readings = read_jsonl(STOPS_READ)
        if stale(readings, read_jsonl(TRIALS)):
            print(
                f"{STOPS_READ.relative_to(EVAL.parent)} was read from other labels: run eval.phone read",
                file=sys.stderr,
            )
            return 1
        claims = stop_claims(readings) if a.what == "stops" else stop_ms_claims(read_jsonl(TIMINGS_READ), readings)
        for c in claims:
            print(json.dumps(c, ensure_ascii=False))
        return 0
    sessions = a.sessions or private_root("sessions")
    if sessions is None:
        print("eval.phone needs FIXIT_PRIVATE (or --sessions DIR)", file=sys.stderr)
        return 2
    if a.what == "gap":
        lines, missing = fill_gap(read_jsonl(GAP_STOPS), sessions)
        write_jsonl(GAP_STOPS, lines)
        for sid in missing:
            print(f"no g1 stop with a zone for gap stop {sid}", file=sys.stderr)
        return 1 if missing else 0
    write_jsonl(STOPS_READ, read_trials(read_jsonl(TRIALS), sessions))
    runs = a.runs or private_root("runs/phone")
    if runs is not None and runs.exists():
        write_jsonl(TIMINGS_READ, read_timings(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
