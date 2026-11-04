"""No off switch anywhere. The grep is planted and caught."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", "dist", "build"}
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".toml", ".yaml", ".yml", ".json", ".env", ".cfg", ".ini", ".sh"}
# An env var, setting, flag or URL param whose name says it skips, disables or bypasses a safety gate.
SWITCH = re.compile(
    r"""(?ix)
    (?:FIXIT_)?(?:no|skip|disable|bypass|ignore|unsafe|allow_unsafe)[_-]?(?:safety|rules|g0|g1|gate|guard|filter|stops?)
      (?![_-]?(?:file|for))  # "no rules file", "no rules for country": the fail-closed cases, not switches
    |(?:safety|rules|g0|g1|gate|guard|stops?)[_-]?(?:off|disabled?|bypass|skip|enabled\s*[=:]\s*(?:false|0))
    |[?&](?:nosafety|safety=off|rules=off|g0=off|g1=off|unsafe=1)
    """
)
EXEMPT = {Path("tests/l4/test_no_off_switch.py")}


def offenders(root: Path) -> list[str]:
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_dir() or SKIP & set(rel.parts) or rel in EXEMPT:
            continue
        if p.suffix not in SUFFIXES and not p.name.startswith(".env"):
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if SWITCH.search(line):
                out.append(f"{rel}:{n}: {line.strip()[:100]}")
    return out


def test_no_off_switch_in_the_repo() -> None:
    assert offenders(ROOT) == []


def test_the_grep_catches_a_planted_switch(tmp_path: Path) -> None:
    planted = {
        "app/settings.py": 'SKIP_SAFETY = os.environ.get("FIXIT_SKIP_SAFETY")',
        "phone/src/x.ts": "if (params.get('g1') === 'off') {} const url = '/?g1=off'",
        "config/a.toml": "rules_enabled = false",
        "app/b.py": "def check(bypass_g0=True): ...",
        ".env.example": "DISABLE_RULES=1",
    }
    for rel, text in planted.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text + "\n")
    found = offenders(tmp_path)
    assert {f.split(":")[0] for f in found} == set(planted), found


def test_rules_import_nothing_from_the_rest_of_the_repo() -> None:
    import ast

    for p in (ROOT / "rules").rglob("*.py"):
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            for m in mods:
                assert m.split(".")[0] not in {"app", "ingest", "eval", "calls", "local", "openai"}, f"{p}: {m}"
