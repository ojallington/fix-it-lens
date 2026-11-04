"""The package boundary.

- Only `calls/` imports `openai` or names a model endpoint (`api.meta.ai`, the ollama port 11434).
  Other HTTP, such as maker sites and Home Assistant, is fine anywhere.
- Only `app/` imports `rules`; `rules/` never imports `app`.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".svelte", ".vue"}
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", "dist", "build"}
# The boundary's own checker and its tests name what they look for.
EXEMPT = {Path("tools/boundary.py"), Path("tests/l0/test_boundary.py")}
MODEL_ENDPOINT = re.compile(r"api\.meta\.ai|(?<![\d.])11434(?!\d)")
TS_OPENAI = re.compile(r"""(?:from\s+|require\(\s*|import\(\s*)['"]openai(?:/[^'"]*)?['"]""")
TS_RULES = re.compile(r"""(?:from\s+|require\(\s*|import\(\s*)['"](?:\.\./)*rules(?:/[^'"]*)?['"]""")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


def _top(path: Path) -> str:
    return path.parts[0] if len(path.parts) > 1 else ""


def _py_imports(source: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.lineno, node.module.split(".")[0]))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) in {"import_module", "__import__"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.append((node.lineno, node.args[0].value.split(".")[0]))
    return out


def check_file(rel: Path, source: str) -> list[Violation]:
    top = _top(rel)
    found: list[Violation] = []
    if top != "calls":
        for n, line in enumerate(source.splitlines(), 1):
            if MODEL_ENDPOINT.search(line):
                found.append(Violation(rel, n, "names a model endpoint outside calls/"))
    if rel.suffix == ".py":
        for n, mod in _py_imports(source):
            if mod == "openai" and top != "calls":
                found.append(Violation(rel, n, "imports openai outside calls/"))
            if mod == "rules" and top not in {"app", "rules", "tests"}:
                found.append(Violation(rel, n, "imports rules/ (only app/ may)"))
            if mod == "app" and top == "rules":
                found.append(Violation(rel, n, "rules/ imports app/ (never the reverse)"))
    else:
        for n, line in enumerate(source.splitlines(), 1):
            if TS_OPENAI.search(line):
                found.append(Violation(rel, n, "imports openai outside calls/"))
            if TS_RULES.search(line) and top != "app":
                found.append(Violation(rel, n, "imports rules/ (only app/ may)"))
    return found


def scan(root: Path) -> list[Violation]:
    found: list[Violation] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(p in SKIP_DIRS for p in rel.parts) or rel in EXEMPT:
            continue
        if path.is_file() and path.suffix in CODE_SUFFIXES:
            found.extend(check_file(rel, path.read_text(encoding="utf-8", errors="ignore")))
    return found


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    found = scan(root)
    for v in found:
        print(f"boundary: {v}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
