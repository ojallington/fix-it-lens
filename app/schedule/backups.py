"""Copies of the store made before an upgrade (FIXIT_PRIVATE/store.before-*.sqlite) are removed 7 days after they
were made: when the server starts, and once a day while it runs. The log names each file removed and counts them; a
backup is never opened or read here. Only regular files that match the pattern go; the store itself never does.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("fixit.backups")

KEEP = timedelta(days=7)
PATTERN = "store.before-*.sqlite"
DAY_S = 24 * 3600.0


def sweep(private: Path, now: datetime | None = None, keep: timedelta = KEEP) -> list[str]:
    """Remove the store backups in `private` made `keep` or longer ago (by the file's own time); their names."""
    cutoff = (now or datetime.now(UTC)).timestamp() - keep.total_seconds()
    removed: list[str] = []
    for f in sorted(private.glob(PATTERN)):
        if f.is_symlink() or not f.is_file() or f.stat().st_mtime > cutoff:
            continue
        f.unlink()
        removed.append(f.name)
    if removed:
        log.info("removed %d store backup(s) older than %d days: %s", len(removed), keep.days, ", ".join(removed))
    return removed


class Sweeper:
    """Sweep now, then every `every_s` on a daemon thread until stopped; a failed sweep is logged and the next one
    still runs."""

    def __init__(self, private: Path, every_s: float = DAY_S) -> None:
        self.private, self.every_s = private, every_s
        self._stop = threading.Event()

    def _once(self) -> None:
        try:
            sweep(self.private)
        except OSError:
            log.exception("store backup sweep failed")

    def _loop(self) -> None:
        while not self._stop.wait(self.every_s):
            self._once()

    def start(self) -> Sweeper:
        self._once()
        threading.Thread(target=self._loop, name="backup-sweep", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
