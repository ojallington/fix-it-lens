"""Generate the pydantic v2 models and TypeScript types from contracts/*.schema.json.

    python tools/gen_contracts.py           # write contracts/models/*.py and contracts/ts/src/*.ts
    python tools/gen_contracts.py --check   # fail if the committed types are not current
"""

from __future__ import annotations

import filecmp
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
NAMES = ["card", "rules", "g1", "safety", "tools", "judge", "ws", "session", "ledger", "claim", "feedback"]
CODEGEN = [
    "--input-file-type", "jsonschema",
    "--output-model-type", "pydantic_v2.BaseModel",
    "--target-python-version", "3.12",
    "--use-annotated", "--field-constraints", "--use-standard-collections", "--use-union-operator",
    "--disable-timestamp", "--use-double-quotes", "--use-schema-description", "--use-field-description",
    "--enum-field-as-literal", "all", "--use-title-as-name", "--collapse-root-models",
    "--formatters", "black", "isort",
]
INIT = '''"""Generated from contracts/*.schema.json by `make contracts`. Do not edit."""
'''


def gen_python(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "__init__.py").write_text(INIT)
    for name in NAMES:
        subprocess.run(
            [sys.executable, "-m", "datamodel_code_generator", "--input", str(CONTRACTS / f"{name}.schema.json"),
             "--output", str(out / f"{name}.py"), *CODEGEN],
            check=True, cwd=CONTRACTS, capture_output=True,
        )


def gen_ts(out: Path) -> None:
    subprocess.run(["node", "gen.mjs", str(out)], check=True, cwd=CONTRACTS / "ts")


def same_tree(a: Path, b: Path, pattern: str) -> list[str]:
    names = {p.name for p in a.glob(pattern)} | {p.name for p in b.glob(pattern)}
    return sorted(n for n in names if not ((a / n).exists() and (b / n).exists()
                                           and filecmp.cmp(a / n, b / n, shallow=False)))


def main() -> int:
    check = "--check" in sys.argv
    py_dir, ts_dir = CONTRACTS / "models", CONTRACTS / "ts" / "src"
    if not check:
        gen_python(py_dir)
        gen_ts(ts_dir)
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        gen_python(t / "py")
        gen_ts(t / "ts")
        stale = [f"contracts/models/{n}" for n in same_tree(py_dir, t / "py", "*.py")]
        generated_ts = {p.name for p in (t / "ts").glob("*.ts")}
        if generated_ts != {f"{n}.ts" for n in NAMES} | {"index.ts"}:
            print("contracts: the TypeScript generator wrote", sorted(generated_ts), file=sys.stderr)
            return 1
        stale += [f"contracts/ts/src/{n}" for n in same_tree(ts_dir, t / "ts", "*.ts") if n in generated_ts]
        if stale:
            print("contracts: generated types are stale; run `make contracts`:", *stale, sep="\n  ", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
