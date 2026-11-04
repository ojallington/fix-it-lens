"""Prompt adaptations for local mode ("prompts are adapted only where Glimmer needs it").

Each local/prompts/<role>.md names the sha256 of the exact upstream text it replaces. The swap happens when the
body is built, so the request hash (and every replay fixture) follows the adapted text. An upstream text that has
changed no longer matches, so Glimmer gets it unadapted and tests/l7 fails until the adaptation is redone.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from calls.errors import ConfigError


@dataclass(frozen=True)
class Adaptation:
    role: str
    adapts: str  # sha256 of the upstream text
    text: str


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def parse(path: Path) -> Adaptation:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ConfigError(f"{path}: no front matter")
    head, text = raw[4:].split("\n---\n", 1)
    meta = dict(line.split(": ", 1) for line in head.splitlines() if line.strip())
    if meta.get("role") != path.stem or len(meta.get("adapts", "")) != 64:
        raise ConfigError(f"{path}: front matter needs role: {path.stem} and adapts: <sha256>")
    return Adaptation(meta["role"], meta["adapts"], text.strip())


@cache
def load(prompts_dir: Path) -> dict[str, Adaptation]:
    if not prompts_dir.is_dir():
        return {}
    return {p.stem: parse(p) for p in sorted(prompts_dir.glob("*.md")) if p.name != "README.md"}


def apply(prompts_dir: Path, role: str, instructions: str | None,
          items: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    a = load(prompts_dir).get(role)
    if a is None:
        return instructions, items
    if instructions is not None and sha(instructions) == a.adapts:
        instructions = a.text
    out = []
    for it in items:
        if it.get("type", "message") == "message" and it.get("role") in ("developer", "system") \
                and isinstance(it.get("content"), list):
            parts = [{**c, "text": a.text} if c.get("type") == "input_text" and sha(str(c.get("text"))) == a.adapts
                     else c for c in it["content"]]
            it = {**it, "content": parts}
        out.append(it)
    return instructions, out
