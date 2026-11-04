"""Record and replay: fixtures/<lane>/<request_sha256>.jsonl hold responses only, never request bodies.

A fixture is a FixtureHeader line, then one FixtureEvent line per streamed event ({t_ms, event}), as in
contracts/session.schema.json. Request bodies (they hold frames and manual text) are kept only in record mode, only in
FIXIT_PRIVATE/requests/<sha>.json, 0600, and go 7 days later with their replies (calls/retention.py). Response
objects are stripped of every request echo before they are written.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from calls.errors import MissingFixture

# Fields a Responses `response` object echoes back from the request. Never written to a fixture.
REQUEST_ECHOES = frozenset({"input", "instructions", "tools", "text", "metadata", "prompt", "reasoning", "include",
                            "tool_choice", "user", "safety_identifier", "prompt_cache_key"})


def canonical(body: Any) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def request_sha256(body: Any) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


def sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Drop request echoes from an SSE event's embedded response object."""
    out = dict(event)
    resp = out.get("response")
    if isinstance(resp, dict):
        out["response"] = {k: v for k, v in resp.items() if k not in REQUEST_ECHOES}
    return out


@dataclass(frozen=True)
class Fixture:
    header: dict[str, Any]
    events: list[tuple[float, dict[str, Any]]]


def fixture_path(root: Path, lane: str, sha: str) -> Path:
    return root / lane / f"{sha}.jsonl"


def find_fixture(roots: list[Path], sha: str) -> Path:
    for root in roots:
        if root.is_dir():
            for p in root.glob(f"*/{sha}.jsonl"):
                return p
    where = ", ".join(str(r) for r in roots)
    raise MissingFixture(f"no fixture for request {sha} under {where}; record it with FIXIT_CALLS=record")


def read_fixture(path: Path) -> Fixture:
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    if not lines or lines[0].get("fixture") != 1:
        raise MissingFixture(f"{path} is not a fixture (no header line)")
    return Fixture(lines[0], [(float(x["t_ms"]), x["event"]) for x in lines[1:]])


def write_fixture(
    root: Path,
    lane: str,
    sha: str,
    *,
    kind: str,
    role: str,
    params: dict[str, Any],
    events: list[tuple[float, dict[str, Any]]],
    response_model: str | None,
    latency_ms: float | None,
    usd: float | None,
    http_status: int | None = 200,
) -> Path:
    path = fixture_path(root, lane, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "fixture": 1, "request_sha256": sha, "kind": kind, "role": role, "params": params,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"), "response_model": response_model,
        "latency_ms": latency_ms, "usd": usd, "http_status": http_status,
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for t_ms, ev in events:
            f.write(json.dumps({"t_ms": round(t_ms, 1), "event": sanitize_event(ev)}, ensure_ascii=False) + "\n")
    return path


def save_request_body(private: Path | None, sha: str, body: Any) -> None:
    """Record mode only: the body beside its fixture, in the private folder, 0600. A live call keeps no body (it
    holds a household's frames, audio hash or manual), only its hash in the ledger."""
    if private is None:
        return
    d = private / "requests"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha}.json"
    if not p.exists():
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(canonical(body))
