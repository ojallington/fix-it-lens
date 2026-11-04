"""Hazard to STOP: how long from the end of a spoken hazard line to STOP on the phone.

    python -m eval.hazard e2e              # hazard.e2e.*: read off filmed clips (the number the video may show)
    python -m eval.hazard server           # hazard.server.stopped: every line stops the server's session
    python -m eval.hazard server-latency   # hazard.server.p95_ms: words in to STOP out, server side only
    python -m eval.hazard sheet            # the filming sheet: the 20 lines and the setup, as Markdown

The lines are eval/hazard.toml: 20 spoken lines (10 English, 10 Dutch), each naming a hazard the rules stop on.

End to end (e2e). The phone is filmed, screen and sound, at 60 fps while a speaker reads each line in a fresh
session. The read-off page (`python -m eval.label.server`, then /latency) steps the clip frame by frame over its
waveform; the labeller marks where the spoken line ends and the first frame that shows STOP. Each reading is a line
of eval/labels/hazard_latency.jsonl: {clip_id, sha256, line_id, speech_end_ms, stop_shown_ms (null: no STOP within
the clip), fps, phone, network}. The clips stay in FIXIT_PRIVATE/clips-raw/hazard/; the repo holds the readings.
This includes the phone's speech capture, the upload, transcription, the rules and drawing STOP.

Server side (server, server-latency). Each line goes as typed text into a fresh app session (the production
utterance path: the rules' hazard check, the safety message, the stop script) and the time to the safety message is
taken. Transcription, the network and the phone are not in it, so it is a regression check, and shown only as
"server side" if at all.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.stats import percentile, wilson_count

EVAL = Path(__file__).resolve().parent
SCRIPT = EVAL / "hazard.toml"
READINGS = EVAL / "labels" / "hazard_latency.jsonl"
FPS = 60
E2E_UNIT = "ms from the end of the spoken line to STOP on the phone"
SERVER_UNIT = "ms from the words reaching the server to its STOP message"


@dataclass(frozen=True)
class Line:
    id: str
    lang: str
    zone: str
    text: str


def lines(path: Path = SCRIPT) -> list[Line]:
    return [Line(**x) for x in tomllib.loads(path.read_text())["line"]]


def readings(path: Path = READINGS) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def e2e_claims(rs: list[dict[str, Any]], script: list[Line]) -> list[dict[str, Any]]:
    """A line read more than once counts once (its last reading); a line not in the script is an error."""
    known = {x.id for x in script}
    last: dict[str, dict[str, Any]] = {}
    for r in rs:
        if r["line_id"] not in known:
            raise ValueError(f"reading for {r['line_id']!r}, which is not in eval/hazard.toml")
        last[r["line_id"]] = r
    n = len(last)
    ms = [r["stop_shown_ms"] - r["speech_end_ms"] for r in last.values() if r["stop_shown_ms"] is not None]
    k = len(ms)
    setup = sorted({f"{r.get('phone')}, {r.get('network')}, {r.get('fps', FPS)} fps" for r in last.values()})
    note = (f"read off {n} filmed lines frame by frame ({'; '.join(setup)}); a 60 fps frame is 16.7 ms"
            if n else "")
    out: list[dict[str, Any]] = [
        {"id": "hazard.e2e.stopped", "value": k, "unit": "spoken hazard lines of n that showed STOP", "n": n,
         "ci": list(wilson_count(k, n)) if n else None},
        {"id": "hazard.e2e.p95_ms", "value": round(percentile(ms, 95)) if ms else None, "unit": E2E_UNIT, "n": k,
         "ci": None},
        {"id": "hazard.e2e.max_ms", "value": round(max(ms)) if ms else None, "unit": E2E_UNIT, "n": k, "ci": None},
    ]
    for c in out:
        if note:
            c["note"] = note
    return out


async def server_run(script: list[Line]) -> list[tuple[Line, float | None]]:
    """(line, ms to the STOP safety message, or None when the line did not stop the session for its own hazard: a
    stop under another zone's rule is a miss, so a line that only stops by accident is caught)."""
    from app.session import Session

    out: list[tuple[Line, float | None]] = []
    for i, line in enumerate(script):
        s = Session(session_id=f"hazard-{line.id}", client=None, lang=line.lang, country="nl")  # type: ignore[arg-type]
        stop_at: list[float] = []

        async def sink(m: dict[str, Any], stop_at: list[float] = stop_at, zone: str = line.zone) -> None:
            st = m.get("state") or {}
            if m.get("type") == "safety" and st.get("state") == "STOP" and not stop_at:
                stop_at.append(time.perf_counter())
                if not str(st.get("rule_id", "")).endswith(f".{zone}"):
                    stop_at.append(-1.0)  # stopped, but under another rule

        s.sinks.append(sink)
        t0 = time.perf_counter()
        await s.handle({"type": "utterance", "seq": i, "t_ms": 0.0, "utterance_id": f"u-{line.id}",
                        "lang": line.lang, "text": line.text, "audio_wav_b64": None})
        await asyncio.gather(*list(s._voice))
        await s.close()
        ok = len(stop_at) == 1
        out.append((line, round((stop_at[0] - t0) * 1000, 3) if ok else None))
    return out


def server_claims(results: list[tuple[Line, float | None]], *, latency: bool) -> list[dict[str, Any]]:
    n = len(results)
    ms = [m for _, m in results if m is not None]
    if latency:
        return [{"id": "hazard.server.p95_ms", "value": round(percentile(ms, 95), 2) if ms else None,
                 "unit": SERVER_UNIT, "n": len(ms), "ci": None,
                 "note": "typed text into a fresh session; no transcription, network or phone"}]
    k = len(ms)
    missed = [line.id for line, m in results if m is None]
    return [{"id": "hazard.server.stopped", "value": k, "unit": "spoken hazard lines of n that stop the session",
             "n": n, "ci": list(wilson_count(k, n)) if n else None,
             "note": "the 20 lines of eval/hazard.toml as typed text"
             + (f"; did not stop for their own hazard: {missed}" if missed else "")}]


def sheet(script: list[Line]) -> str:
    out = ["# Hazard to STOP: filming sheet", "",
           "Spoken lines only. Nothing is staged: no fault, no smoke, no water, no running machine, and the gas tap "
           "is never touched. The speaker stands or sits by the appliance and only talks to the phone.", "",
           "## Setup", "",
           "- A second camera films the phone's screen **with sound**, at **60 fps**, steady on a stand, the screen "
           "filling most of the frame. Its microphone must hear the speaker clearly.",
           "- The phone runs Fix-It Lens as a household would (the hosted app, on the home wifi). Note the phone "
           "model and the network for the readings.",
           "- One continuous take is fine. Before each line, start a **new session** on the phone (a STOP cannot be "
           "cleared inside a session), wait for the guide to be listening, then read the line once, plainly.",
           "- Say the line id first (\"h01\"), pause a second, then the line, so the read-off finds it.",
           "- Wait until STOP shows (or 10 s), then the next line.",
           "- Only the screen and the speaker's voice in frame: no face, no plates, no house numbers.", "",
           "## Lines", "", "| id | language | line |", "|---|---|---|"]
    out += [f"| {x.id} | {x.lang} | {x.text} |" for x in script]
    out += ["", "Afterwards: copy the clip to FIXIT_PRIVATE/clips-raw/hazard/ and read it off with "
            "`python -m eval.label.server`, page /latency.", ""]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.hazard")
    ap.add_argument("what", choices=["e2e", "server", "server-latency", "sheet"])
    a = ap.parse_args(argv)
    script = lines()
    if a.what == "sheet":
        print(sheet(script), end="")
        return 0
    if a.what == "e2e":
        claims = e2e_claims(readings(), script)
    else:
        claims = server_claims(asyncio.run(server_run(script)), latency=a.what == "server-latency")
    for c in claims:
        print(json.dumps(c, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
