"""The app image (deploy/Dockerfile) takes every folder of this repo that the code in it imports."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_image_copies_every_folder_its_code_imports() -> None:
    dockerfile = (ROOT / "deploy/Dockerfile").read_text()
    copied = set(re.findall(r"^COPY (\w+) \1$", dockerfile, re.M))
    allowed = (ROOT / ".dockerignore").read_text()
    imported: dict[str, str] = {}
    for top in sorted(copied):
        for py in (ROOT / top).rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                         [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
                for name in names:
                    head = name.split(".")[0]
                    if (ROOT / head).is_dir() and head not in copied:
                        imported.setdefault(head, str(py.relative_to(ROOT)))
    assert imported == {}, f"imported but not in the image: {imported}"
    assert all(f"!{top}/" in allowed for top in copied)
