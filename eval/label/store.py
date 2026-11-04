"""The labelling tool's logic: capture sessions in, labelled clips out. No HTTP here, so it is tested directly.

A capture session (the app's capture mode: frames recorded, no model calls) lives in FIXIT_PRIVATE/sessions/<id>/ with
meta.json, events.jsonl and frames/<frame_id>.jpg. Labelling one assigns it to a planned clip id, picks the BEFORE
frame and the clip window, confirms the truth (or relabels it with a reason), and records that consent is signed
and the frames were scanned for faces, plates and house identifiers. Saving it:

- copies the window's frames to FIXIT_PRIVATE/clips-raw/<clip_id>/ (the clip, self-contained, never in the repo);
- upserts one line in eval/labels/stepcheck.jsonl with the label and the sha256 of every frame (the manifest).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parent.parent
PLAN = EVAL / "labels" / "stepcheck.plan.jsonl"
LABELS = EVAL / "labels" / "stepcheck.jsonl"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
TRUTHS = ("done", "not_done", "cant_see")


class LabelError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    frame_id: str
    t_ms: float
    path: Path

    @property
    def t(self) -> float | None:
        """t_ms for JSON: None when the session has no frame event for it."""
        return None if math.isnan(self.t_ms) else self.t_ms


def _safe(s: str, what: str) -> str:
    if not SAFE_ID.match(s) or s in {".", ".."}:
        raise LabelError(f"bad {what}: {s!r}")
    return s


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def plan(path: Path = PLAN) -> dict[str, dict[str, Any]]:
    return {c["clip_id"]: c for c in read_jsonl(path)}


def capture_sessions(private: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    root = private / "sessions"
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("mode") != "capture":
            continue
        out.append({"session_id": d.name, "started_at": meta.get("started_at"),
                    "frames": len(list((d / "frames").glob("*.jpg")))})
    return out


def session_frames(private: Path, session_id: str) -> list[Frame]:
    d = private / "sessions" / _safe(session_id, "session id")
    times: dict[str, float] = {}
    for ev in read_jsonl(d / "events.jsonl"):
        msg = ev.get("msg") or {}
        if ev.get("kind") == "frame" and "frame_id" in msg:
            times[str(msg["frame_id"])] = float(msg.get("t_ms", ev.get("t_ms", 0)))
    frames = []
    for p in (d / "frames").glob("*.jpg"):
        frames.append(Frame(p.stem, times.get(p.stem, float("nan")), p))
    # events order is capture order; frames without an event (should not happen) sort by name after them
    frames.sort(key=lambda f: (math.isnan(f.t_ms), 0.0 if math.isnan(f.t_ms) else f.t_ms, f.frame_id))
    return frames


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_label(private: Path, form: dict[str, Any], *, plan_path: Path = PLAN, labels_path: Path = LABELS,
               now: datetime | None = None) -> dict[str, Any]:
    clips = plan(plan_path)
    clip_id = _safe(str(form.get("clip_id", "")), "clip id")
    planned = clips.get(clip_id)
    if planned is None:
        raise LabelError(f"{clip_id} is not in the plan")
    truth = form.get("truth")
    if truth not in TRUTHS:
        raise LabelError(f"truth must be one of {TRUTHS}")
    reason = str(form.get("relabel_reason") or "").strip()
    if truth != planned["truth"] and not reason:
        raise LabelError(f"truth {truth} differs from the plan ({planned['truth']}): give the reason")
    if not form.get("consent"):
        raise LabelError("no clip enters the eval without signed consent from everyone whose hands appear")
    if not form.get("scanned"):
        raise LabelError("scan the frames for faces, plates and house identifiers first")
    session_id = _safe(str(form.get("session_id", "")), "session id")
    frames = session_frames(private, session_id)
    ids = [f.frame_id for f in frames]
    try:
        before = ids.index(str(form["before"]))
        start, end = ids.index(str(form["start"])), ids.index(str(form["end"]))
    except (KeyError, ValueError) as e:
        raise LabelError(f"before/start/end must be frames of session {session_id}") from e
    if not before <= start <= end:
        raise LabelError("need BEFORE <= window start <= window end")
    chosen = [frames[before]] + frames[start:end + 1]
    out_dir = private / "clips-raw" / clip_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "frames").mkdir(parents=True)
    manifest = []
    for i, f in enumerate(chosen):
        role = "before" if i == 0 else "now"
        name = f"{i:03d}_{role}.jpg"
        shutil.copyfile(f.path, out_dir / "frames" / name)
        manifest.append({"file": name, "role": role, "t_ms": f.t, "sha256": sha256(f.path)})
    line = {
        "clip_id": clip_id, "step_id": planned["step_id"], "appliance": planned["appliance"], "take": planned["take"],
        "truth": truth, "planned_truth": planned["truth"], "relabel_reason": reason or None,
        "guarded": planned["guarded"], "session_id": session_id, "frames": manifest,
        "consent": str(form["consent"]), "scanned": True, "note": str(form.get("note") or "") or None,
        "labelled_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
    }
    (out_dir / "clip.json").write_text(json.dumps(line, indent=1, ensure_ascii=False) + "\n")
    rows = [r for r in read_jsonl(labels_path) if r["clip_id"] != clip_id] + [line]
    rows.sort(key=lambda r: list(clips).index(r["clip_id"]) if r["clip_id"] in clips else 10**6)
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return line


def state(private: Path, plan_path: Path = PLAN, labels_path: Path = LABELS) -> dict[str, Any]:
    labelled = {r["clip_id"]: r for r in read_jsonl(labels_path)}
    used = {r["session_id"]: r["clip_id"] for r in labelled.values()}
    sessions = [{**s, "clip_id": used.get(s["session_id"])} for s in capture_sessions(private)]
    return {"plan": list(plan(plan_path).values()), "labels": labelled, "sessions": sessions}
