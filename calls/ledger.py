"""The spend ledger: one line per call in FIXIT_PRIVATE/ledger.jsonl, a hard cap, and lane budgets.

Several processes write one ledger, so every read-check and every append holds an exclusive flock on it. The check
runs before any network I/O and refuses a call whose worst-case estimate would pass the lane's budget or the total
cap (budgets.toml). Concurrent callers can each pass the check before either appends, so the overshoot is bounded
by the in-flight estimates (cents), never by an unbounded run.
"""

from __future__ import annotations

import fcntl
import json
import os
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from calls.errors import BudgetExceeded

LEDGER_FIELDS = (
    "ts", "run", "probe", "note", "role", "call_id", "request_sha256", "model", "response_model", "status",
    "incomplete", "store", "latency_s", "ttft_s", "first_event_s", "input_tokens", "cached_tokens",
    "output_tokens", "reasoning_tokens", "images_processed", "video_frames_processed", "audio_seconds", "usd",
    "error", "lane", "household", "session_id", "mode", "provider", "origin",
)


@dataclass(frozen=True)
class Totals:
    total: float
    by_lane: dict[str, float]


@dataclass(frozen=True)
class Spend:
    by_provider: dict[str, float]
    meta_public: float


class Ledger:
    def __init__(self, path: Path, budgets_path: Path) -> None:
        self.path = path
        self.budgets_path = budgets_path

    def budgets(self) -> tuple[float, dict[str, float]]:
        data = tomllib.loads(self.budgets_path.read_text())
        return float(data["total_cap"]), {k: float(v) for k, v in data.get("lanes", {}).items()}

    @contextmanager
    def _locked(self) -> Iterator[IO[str]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield f
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    @staticmethod
    def _totals(f: IO[str]) -> Totals:
        f.seek(0)
        total = 0.0
        by_lane: dict[str, float] = {}
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            usd = float(row.get("usd") or 0.0)
            total += usd
            lane = str(row.get("lane") or "unknown")
            by_lane[lane] = by_lane.get(lane, 0.0) + usd
        return Totals(total, by_lane)

    @staticmethod
    def _household_today(f: IO[str], household: str) -> float:
        f.seek(0)
        day = datetime.now(UTC).date().isoformat()
        usd = 0.0
        for line in f:
            if line.strip():
                row = json.loads(line)
                if row.get("household") == household and str(row.get("ts") or "").startswith(day):
                    usd += float(row.get("usd") or 0.0)
        return usd

    def spend(self) -> Spend:
        """What each provider has served (a line with no provider is Meta's), and Meta's share of it from public
        sessions (lines whose origin is door or judges)."""
        by: dict[str, float] = {}
        public = 0.0
        if not self.path.exists():
            return Spend(by, public)
        with self._locked() as f:
            f.seek(0)
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                usd = float(row.get("usd") or 0.0)
                who = str(row.get("provider") or "meta")
                by[who] = by.get(who, 0.0) + usd
                if who == "meta" and row.get("origin") in ("door", "judges"):
                    public += usd
        return Spend(by, public)

    def totals(self) -> Totals:
        with self._locked() as f:
            return self._totals(f)

    def check(self, lane: str, estimate_usd: float, *, household: str | None = None,
              household_cap_usd: float | None = None) -> Totals:
        """Refuse (BudgetExceeded) if the call could pass the lane budget, the cap, or (when the call carries a
        household and its cap, households.cap_usd_day) that household's spend today (UTC). No network I/O first."""
        cap, budgets = self.budgets()
        if lane not in budgets:
            raise BudgetExceeded(f"lane {lane!r} has no budget in {self.budgets_path.name}")
        with self._locked() as f:
            t = self._totals(f)
            today = self._household_today(f, household) if household and household_cap_usd is not None else 0.0
        if household and household_cap_usd is not None and today + estimate_usd > household_cap_usd:
            raise BudgetExceeded(f"household {household}: ${today:.4f} today + ${estimate_usd:.4f} estimate > "
                                 f"daily cap ${household_cap_usd:.2f}")
        spent = t.by_lane.get(lane, 0.0)
        if spent + estimate_usd > budgets[lane]:
            raise BudgetExceeded(
                f"lane {lane}: ${spent:.4f} spent + ${estimate_usd:.4f} estimate > budget ${budgets[lane]:.2f}"
            )
        if t.total + estimate_usd > cap:
            raise BudgetExceeded(f"total: ${t.total:.4f} spent + ${estimate_usd:.4f} estimate > cap ${cap:.2f}")
        return t

    def forget_household(self, household_id: str) -> int:
        """Set household and session_id to null on every line of this household (its data is deleted, and the session
        ids name recordings that are gone). Every line stays, with its usd, so totals and caps do not move. Under the
        flock, in place: a shorter line is padded with spaces to its old length, so a crash midway leaves whole lines;
        only if a line would grow is the file rewritten. Returns how many lines changed."""
        if not household_id:
            raise ValueError("forget_household needs a household id")
        with self._locked():
            fd = os.open(self.path, os.O_RDWR)                # no O_APPEND: pwrite must land where the line was
            try:
                data = os.pread(fd, os.fstat(fd).st_size, 0)
                lines = data.split(b"\n")
                edits: list[tuple[int, bytes]] = []
                out: list[bytes] = []
                offset = 0
                grew = False
                for raw in lines:
                    new = raw
                    if raw.strip():
                        row = json.loads(raw)
                        if row.get("household") == household_id:
                            row["household"] = None
                            row["session_id"] = None
                            new = json.dumps(row, ensure_ascii=False).encode()
                            grew = grew or len(new) > len(raw)
                            new = new.ljust(len(raw))
                            edits.append((offset, new))
                    out.append(new)
                    offset += len(raw) + 1
                if not edits:
                    return 0
                if grew:
                    whole = b"\n".join(out)
                    os.pwrite(fd, whole, 0)
                    os.ftruncate(fd, len(whole))
                else:
                    for o, new in edits:
                        os.pwrite(fd, new, o)
                os.fsync(fd)
                return len(edits)
            finally:
                os.close(fd)

    def append(self, row: dict[str, Any]) -> dict[str, Any]:
        line = {k: row.get(k) for k in LEDGER_FIELDS}
        line["ts"] = line["ts"] or datetime.now(UTC).isoformat(timespec="seconds")
        line["store"] = False
        with self._locked() as f:
            f.seek(0, os.SEEK_END)
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return line
