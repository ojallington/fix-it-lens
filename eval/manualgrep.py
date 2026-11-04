"""The fixture grep (c9 row 37): no recorded response in the repo may carry manual text. Every string in every
recorded file is checked against the private manuals' page text with a 40-character shingle match, after folding
case and whitespace, so a copied sentence is caught however it was re-wrapped. A shingle found in the manuals of
two or more makers (manuals/<maker>/...) is a generic phrase, such as "de stekker uit het stopcontact voordat",
not one manual's text, and is left out; a shingle from one maker's manuals alone is always checked.

    python -m eval.manualgrep [paths...]    # default: fixtures/ and eval/results/; exits 1 on any hit

Needs FIXIT_PRIVATE/manuals/ (CI has no manuals, so this runs on the build machine before each merge).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHINGLE = 40
DEFAULT_SCAN = ("fixtures", "eval/results", "local/recorded")
PUBLIC_DUMMIES = ("acme",)  # the obviously fake "Acme WM-0000": its text is public by design
_WS = re.compile(r"\s+")


def fold(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def shingles(text: str, k: int = SHINGLE) -> set[str]:
    t = fold(text)
    return {t[i:i + k] for i in range(len(t) - k + 1)}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    manual: str
    snippet: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.snippet!r} is text from {self.manual}"


def manual_index(manuals: Path) -> dict[str, str]:
    """shingle -> manual name, over every page of every PDF under manuals/, without the generic shingles: those in
    the manuals of 2+ makers (the first folder under manuals/)."""
    from ingest.pages import load  # the private page cache; never imported in CI

    index: dict[str, str] = {}
    maker_of: dict[str, str] = {}
    generic: set[str] = set()
    for pdf in sorted(manuals.rglob("*.pdf")):
        maker = pdf.relative_to(manuals).parts[0]
        if maker in PUBLIC_DUMMIES:
            continue
        name = str(pdf.relative_to(manuals))
        for page in load(pdf):
            for s in shingles(page.text):
                if maker_of.setdefault(s, maker) != maker:
                    generic.add(s)
                index.setdefault(s, f"{name} p{page.number}")
    return {s: m for s, m in index.items() if s not in generic}


def _strings(obj: object) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def scan_file(path: Path, rel: str, index: dict[str, str]) -> list[Hit]:
    hits = []
    for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        try:
            texts: Iterable[str] = list(_strings(json.loads(line)))
        except json.JSONDecodeError:
            texts = [line]
        for t in texts:
            ft = fold(t)
            for i in range(len(ft) - SHINGLE + 1):
                m = index.get(ft[i:i + SHINGLE])
                if m:
                    hits.append(Hit(rel, n, m, ft[i:i + SHINGLE]))
                    break
    return hits


def scan(paths: Iterable[Path], index: dict[str, str], root: Path = ROOT) -> list[Hit]:
    hits: list[Hit] = []
    for p in paths:
        files = [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file() and f.suffix in
                                              {".jsonl", ".json", ".txt", ".md"})
        for f in files:
            hits += scan_file(f, str(f.relative_to(root)) if f.is_relative_to(root) else str(f), index)
    return hits


def main(argv: list[str]) -> int:
    from calls import private_dir

    priv = private_dir()
    if priv is None or not (priv / "manuals").is_dir():
        print("manualgrep: no FIXIT_PRIVATE/manuals here; run it on the build machine")
        return 2
    index = manual_index(priv / "manuals")
    targets = [Path(a) for a in argv] or [ROOT / d for d in DEFAULT_SCAN if (ROOT / d).exists()]
    hits = scan(targets, index)
    for h in hits:
        print(h)
    print(f"manualgrep: {len(hits)} hit(s) over {len(index)} manual shingles")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
