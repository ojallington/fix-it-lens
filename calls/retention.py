"""What record mode keeps in the private folder goes 7 days after it was written: each request body,
FIXIT_PRIVATE/requests/<sha>.json, together with the replies recorded for it, FIXIT_PRIVATE/fixtures/<lane>/<sha>.jsonl
(the served app's and deploy/curate.py's). A body and its replies go in the same sweep, once the newest of them is 7
days old, so a reply is never left without its body or the other way round.

The repo's fixtures/ is never touched, nor any fixtures-<name>/ folder in the private folder: an eval's own (its
replies are what `make eval` replays; a body of one of its calls still goes, since a replay never reads a body), or a
sealed copy, fixtures-sealed-<date>/, kept for a replay past the 7 days. Only regular
files named by a request hash go; a symlink never does. The log names counts, never a body's contents.

    from calls.retention import Sweeper
    Sweeper(private).start()   # sweeps now, then once a day (deploy/serve.py, beside the store backup sweep)

    python -m calls.retention --dry-run [--at 2026-11-03]   # what a sweep would remove now (or at that date), by
                                                            # folder, with the oldest and newest file times
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("fixit.retention")

KEEP = timedelta(days=7)
DAY_S = 24 * 3600.0
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _recorded(private: Path) -> dict[str, list[Path]]:
    """Every body and recorded reply in the private folder, by request hash."""
    found: dict[str, list[Path]] = {}
    for p in [*private.glob("requests/*.json"), *private.glob("fixtures/*/*.jsonl")]:
        if _SHA.match(p.stem) and not p.is_symlink() and p.is_file():
            found.setdefault(p.stem, []).append(p)
    return found


def due(private: Path, now: datetime | None = None, keep: timedelta = KEEP) -> list[Path]:
    """The files a sweep at `now` would remove: each body with its replies once the newest of them is `keep` old (by
    the files' own times). Reads only the files' times."""
    cutoff = (now or datetime.now(UTC)).timestamp() - keep.total_seconds()
    return [f for files in _recorded(private).values() if max(f.stat().st_mtime for f in files) <= cutoff
            for f in files]


def sweep(private: Path, now: datetime | None = None, keep: timedelta = KEEP) -> int:
    """Remove what `due` names; how many files went."""
    gone = 0
    for f in due(private, now, keep):
        f.unlink(missing_ok=True)
        gone += 1
    if gone:
        log.info("removed %d recorded request file(s) older than %d days", gone, keep.days)
    return gone


class Sweeper:
    """Sweep now, then every `every_s` on a daemon thread until stopped; a failed sweep is logged and the next one
    still runs."""

    def __init__(self, private: Path | None, every_s: float = DAY_S) -> None:
        self.private, self.every_s = private, every_s
        self._stop = threading.Event()

    def _once(self) -> None:
        if self.private is None:
            return
        try:
            sweep(self.private)
        except OSError:
            log.exception("recorded request sweep failed")

    def _loop(self) -> None:
        while not self._stop.wait(self.every_s):
            self._once()

    def start(self) -> Sweeper:
        self._once()
        threading.Thread(target=self._loop, name="recorded-sweep", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()


def report(private: Path, now: datetime) -> list[str]:
    """What a sweep at `now` would remove, per folder: the count and the oldest and newest file time (UTC), and what
    would stay."""
    def stamp(t: float) -> str:
        return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M")

    going = set(due(private, now))
    every = [f for files in _recorded(private).values() for f in files]
    lines = [f"at {now.strftime('%Y-%m-%d %H:%M')} UTC: {len(going)} of {len(every)} recorded file(s) would go"]
    for folder in sorted({f.parent for f in every}):
        here = [f for f in every if f.parent == folder]
        gone = [f.stat().st_mtime for f in here if f in going]
        kept = len(here) - len(gone)
        span = f", {stamp(min(gone))} to {stamp(max(gone))}" if gone else ""
        lines.append(f"  {folder.relative_to(private)}/: {len(gone)} go{span}; {kept} stay")
    return lines


def main(argv: list[str] | None = None) -> int:
    import argparse

    from calls import private_dir

    ap = argparse.ArgumentParser(prog="calls.retention", description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", required=True, help="report only; nothing is removed")
    ap.add_argument("--at", action="append", default=[], metavar="YYYY-MM-DD",
                    help="also report a sweep at 00:00 UTC on this date (repeatable)")
    args = ap.parse_args(argv)
    private = private_dir()
    if private is None:
        raise SystemExit("FIXIT_PRIVATE is not set and the default private folder does not exist")
    print(f"{private} (dry run: nothing removed)")
    whens = [datetime.now(UTC)] + [datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=UTC) for d in args.at]
    for when in whens:
        print("\n".join(report(private, when)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
