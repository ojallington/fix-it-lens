"""The repo guard: blocks what must never enter the repo.

Run as the pre-commit hook (`--staged`), in CI (`--all`), or on given paths. It fails on:
  - any PDF;
  - a key-shaped string (the Meta key's prefix pattern, a Hugging Face token);
  - a line of FIXIT_PRIVATE/identifiers.txt, or a serial or product code typed into plates/FIELDS.md
    (both read at run time and never copied; a hit names the file and line, never the value);
  - a `-contributor` model ID;
  - a fixture that holds a request body (fixtures are responses only);
  - an internal build note outside fixtures: a build-lane tag or agent name, a private host, a person's name, a
    dated plan note, or a pointer into the private design library (the repo reads as the product, not the build).

Model codes are public and name the cards, so they are not blocked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_PRIVATE = Path.home() / "Projects/hackathons/fix-it-lens-private"
KEY_PATH = Path.home() / ".config/fleet/meta-model-api.key"
MAX_BYTES = 20 * 1024 * 1024

KEY_PATTERNS = [
    re.compile(r"LLM_\d{3,}_[A-Za-z0-9_\-]{12,}"),  # Meta Model API key prefix pattern
    re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),  # Hugging Face token
]
# A model ID ending in -contributor (prose such as "a `-contributor` ID" does not match).
CONTRIBUTOR_ID = re.compile(r"\b[a-z][a-z0-9.]*(?:-[a-z0-9.]+)*-" + "contributor" + r"\b", re.IGNORECASE)
# Keys that only appear in a request body, never in a recorded response line.
# The words that name private things (hosts, people, internal documents) are not here: they are lines of
# FIXIT_PRIVATE/internal-words.txt, read at run time and scanned the same way.
_DAY = "Mon|Tue|Wed|Thu|Fri|Sat|Sun"
INTERNAL = re.compile("|".join((
    r"\bL[0-7]\b", r"\bfil-[a-z]", r"\bA1[0-9]{2}\b", rf"\b(?:{_DAY}) [0-9]{{1,2}}\b", r"\blane/[a-z]", r"\b[Bb]rief L",
    r"\b100\.(?:6[5-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",  # a tailnet address (100.64.* is the example)
)))
INTERNAL_EXEMPT = ("fixtures/", "uv.lock", "package-lock.json")
# Prose in a fixture folder (its README, notes) is read by people, so it is scanned like any other doc; only the
# recorded data beside it is exempt.
FIXTURE_PROSE = (".md", ".txt", ".rst")
# Directories not yet failed on while their owners scrub them (empty since every owner finished; the list only
# shrinks, so a new entry needs a reason in review). `python tools/guard.py --notes` scans them anyway.
INTERNAL_PENDING: tuple[str, ...] = ()
REQUEST_KEYS = {"input", "instructions", "messages", "tools", "audio", "file_data"}
FIELDS_COLUMNS = ("serial", "product code")
MIN_IDENTIFIER_LEN = 4


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


@dataclass(frozen=True)
class Secrets:
    """Values read from the private folder at run time. Never printed, never written."""

    identifiers: tuple[str, ...] = ()
    plate_values: tuple[str, ...] = ()
    internal_words: re.Pattern[str] | None = None
    key: str | None = None
    notes_everywhere: bool = False  # --notes: also the directories still being scrubbed


def _clean(value: str) -> str:
    return value.strip().strip("`*").strip()


def read_identifiers(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and len(line) >= MIN_IDENTIFIER_LEN:
            out.append(line)
    return tuple(out)


def read_plate_values(path: Path) -> tuple[str, ...]:
    """Serials and product codes from the FIELDS.md table (the columns named in FIELDS_COLUMNS)."""
    if not path.is_file():
        return ()
    values: list[str] = []
    columns: list[int] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        lowered = [c.lower() for c in cells]
        if any(name in c for c in lowered for name in FIELDS_COLUMNS) and not columns:
            columns = [i for i, c in enumerate(lowered) if any(name in c for name in FIELDS_COLUMNS)]
            continue
        if not columns or set(line) <= set("|-: "):
            continue
        for i in columns:
            if i < len(cells):
                for part in re.split(r"[,;]| or ", cells[i]):
                    v = _clean(part)
                    if len(v) >= MIN_IDENTIFIER_LEN:
                        values.append(v)
    return tuple(values)


def read_internal_words(path: Path) -> re.Pattern[str] | None:
    """FIXIT_PRIVATE/internal-words.txt: one regex per line, # for a comment. None when there is no file or no line."""
    if not path.is_file():
        return None
    lines = [s.strip() for s in path.read_text(encoding="utf-8").splitlines()]
    words = [s for s in lines if s and not s.startswith("#")]
    return re.compile("|".join(words)) if words else None


def load_secrets(private: Path | None) -> Secrets:
    key: str | None = None
    try:
        if KEY_PATH.is_file():
            key = KEY_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        key = None
    if private is None or not private.is_dir():
        return Secrets(key=key)
    return Secrets(
        identifiers=read_identifiers(private / "identifiers.txt"),
        plate_values=read_plate_values(private / "plates" / "FIELDS.md"),
        internal_words=read_internal_words(private / "internal-words.txt"),
        key=key,
    )


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _walk_keys(obj: object) -> Iterator[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _walk_strings(obj: object) -> Iterator[str]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def check_fixture(path: str, text: str) -> list[Hit]:
    hits: list[Hit] = []
    docs: list[tuple[int, object]] = []
    if path.endswith(".jsonl"):
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    docs.append((n, json.loads(line)))
                except json.JSONDecodeError:
                    hits.append(Hit(path, n, "fixture line is not JSON"))
    elif path.endswith(".json"):
        try:
            docs.append((1, json.loads(text)))
        except json.JSONDecodeError:
            hits.append(Hit(path, 1, "fixture is not JSON"))
    for n, doc in docs:
        bad = sorted(set(_walk_keys(doc)) & REQUEST_KEYS)
        if bad:
            hits.append(Hit(path, n, f"fixture holds a request body (keys: {', '.join(bad)})"))
        if any(s.startswith("data:") and ";base64," in s[:64] for s in _walk_strings(doc)):
            hits.append(Hit(path, n, "fixture embeds request media (a data: URL)"))
    return hits


def check_blob(path: str, data: bytes, secrets: Secrets) -> list[Hit]:
    hits: list[Hit] = []
    if path.lower().endswith(".pdf") or data[:5] == b"%PDF-":
        return [Hit(path, 0, "a PDF (manuals and PDFs never enter the repo)")]
    text = data.decode("utf-8", errors="ignore")
    idents = [(_norm(v), "identifiers.txt") for v in secrets.identifiers]
    plates = [(_norm(v), "a serial or product code from FIELDS.md") for v in secrets.plate_values]
    exempt = (path.startswith(INTERNAL_EXEMPT) or "/fixtures/" in path) and not path.lower().endswith(FIXTURE_PROSE)
    notes = (b"\0" not in data[:8192] and not exempt
             and (secrets.notes_everywhere or not path.startswith(INTERNAL_PENDING)))
    for n, line in enumerate(text.splitlines(), 1):
        for pat in KEY_PATTERNS:
            if pat.search(line):
                hits.append(Hit(path, n, "a key-shaped string"))
                break
        if secrets.key and secrets.key in line:
            hits.append(Hit(path, n, "the Meta API key"))
        if CONTRIBUTOR_ID.search(line):
            hits.append(Hit(path, n, "a -contributor model ID"))
        if notes and (INTERNAL.search(line) or (secrets.internal_words and secrets.internal_words.search(line))):
            hits.append(Hit(path, n, "an internal build note (lane, agent, host, person, dated plan or library ref)"))
        squashed = _norm(line)
        for value, source in idents + plates:
            if value and value in squashed:
                hits.append(Hit(path, n, f"matches {source}"))
    if path.startswith("fixtures/") or "/fixtures/" in path:
        hits.extend(check_fixture(path, text))
    return hits


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def staged_files() -> Iterator[tuple[str, bytes]]:
    names = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0")
    for raw in names:
        if raw:
            name = raw.decode()
            yield name, _git("show", f":{name}")


def tracked_files() -> Iterator[tuple[str, bytes]]:
    for raw in _git("ls-files", "-z").split(b"\0"):
        if raw:
            name = raw.decode()
            p = Path(name)
            if p.is_file() and not p.is_symlink():
                yield name, p.read_bytes()[:MAX_BYTES]


def given_files(paths: Iterable[str]) -> Iterator[tuple[str, bytes]]:
    for name in paths:
        p = Path(name)
        if p.is_file():
            yield name, p.read_bytes()[:MAX_BYTES]


# The guard's own source and its tests name the patterns they look for.
SELF_EXEMPT = {"tools/guard.py"}


def scan(files: Iterable[tuple[str, bytes]], secrets: Secrets) -> list[Hit]:
    hits: list[Hit] = []
    for name, data in files:
        if name in SELF_EXEMPT:
            continue
        hits.extend(check_blob(name, data, secrets))
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="check the staged files (pre-commit)")
    mode.add_argument("--all", action="store_true", help="check every tracked file (CI)")
    ap.add_argument("--notes", action="store_true",
                    help="also list internal notes under INTERNAL_PENDING (what each owner has left to scrub)")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)

    env = os.environ.get("FIXIT_PRIVATE")
    private = Path(env) if env else (DEFAULT_PRIVATE if DEFAULT_PRIVATE.is_dir() else None)
    secrets = load_secrets(private)
    if args.notes:
        secrets = replace(secrets, notes_everywhere=True)
    if private is None:
        print("guard: no FIXIT_PRIVATE folder; identifier, serial and private-word checks skipped", file=sys.stderr)

    if args.staged:
        files: Iterable[tuple[str, bytes]] = staged_files()
    elif args.all or not args.paths:
        files = tracked_files()
    else:
        files = given_files(args.paths)
    hits = scan(files, secrets)
    for h in hits:
        print(f"guard: {h}", file=sys.stderr)
    if hits:
        print(f"guard: {len(hits)} problem(s); nothing committed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
