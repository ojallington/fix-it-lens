"""The import boundary fails on a planted `import rules` in ingest/ and `import openai` in app/."""

from __future__ import annotations

from pathlib import Path

from tools import boundary


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def test_rules_import_in_ingest_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"ingest/card.py": "import rules\n", "app/loader.py": "from rules import g0\n"})
    found = boundary.scan(root)
    assert [(str(v.path), v.reason) for v in found] == [("ingest/card.py", "imports rules/ (only app/ may)")]


def test_openai_import_in_app_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"app/guide.py": "from openai import AsyncOpenAI\n", "calls/meta.py": "import openai\n"})
    found = boundary.scan(root)
    assert [(str(v.path), v.reason) for v in found] == [("app/guide.py", "imports openai outside calls/")]


def test_reverse_and_endpoints_and_ts(tmp_path: Path) -> None:
    root = _tree(tmp_path, {
        "rules/engine.py": "import app.store\n",
        "eval/run.py": 'URL = "https://api.meta.ai/v1"\n',
        "local/x.py": 'OLLAMA = "http://gpu-host:11434"\n',
        "phone/src/a.ts": "import OpenAI from 'openai';\n",
        "phone/src/b.ts": "import { g0 } from '../../rules/g0';\n",
        "ingest/lookup.py": 'BOSCH = "https://media3.bosch-home.com/x"\n',
    })
    reasons = sorted((str(v.path), v.reason) for v in boundary.scan(root))
    assert reasons == [
        ("eval/run.py", "names a model endpoint outside calls/"),
        ("local/x.py", "names a model endpoint outside calls/"),
        ("phone/src/a.ts", "imports openai outside calls/"),
        ("phone/src/b.ts", "imports rules/ (only app/ may)"),
        ("rules/engine.py", "rules/ imports app/ (never the reverse)"),
    ]


def test_repo_respects_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    assert [str(v) for v in boundary.scan(root)] == []
