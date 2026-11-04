"""local/prompts: each adaptation swaps exactly its upstream text, and only in local mode."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

import calls
from calls import local_prompts
from calls.config import ROOT

PROMPTS = ROOT / "local" / "prompts"
UPSTREAM = {"judge": (ROOT / "app" / "judge.py", "JUDGE_RULES")}


def upstream_text(path: Path, name: str) -> str:
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name:
            return str(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not in {path}")


def dev(text: str) -> dict[str, object]:
    return {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": text}]}


@pytest.mark.parametrize("role", sorted(local_prompts.load(PROMPTS)))
def test_adaptation_still_matches_its_upstream_text(role: str) -> None:
    path, name = UPSTREAM[role]
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} is not merged into this branch yet")
    assert local_prompts.sha(upstream_text(path, name)) == local_prompts.load(PROMPTS)[role].adapts, (
        f"{name} changed upstream: redo and re-measure local/prompts/{role}.md")


def test_swap_happens_in_the_body_only_in_local_mode(tmp_path: Path) -> None:
    a = local_prompts.load(PROMPTS)["judge"]
    upstream = "the api rules"
    fake = tmp_path / "prompts"
    fake.mkdir()
    (fake / "judge.md").write_text(f"---\nrole: judge\nadapts: {local_prompts.sha(upstream)}\n---\n{a.text}\n")
    local = calls.make_client("replay", fixtures_root=tmp_path, private=None, local=True)
    local.config = replace(local.config, prompts_dir=fake)
    body = local.build_respond_body("judge", [dev(upstream), dev("other text")])
    assert body["input"][0]["content"][0]["text"] == a.text
    assert body["input"][1]["content"][0]["text"] == "other text"
    api = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    assert api.build_respond_body("judge", [dev(upstream)])["input"][0]["content"][0]["text"] == upstream
    changed = local.build_respond_body("judge", [dev(upstream + " (edited)")])
    assert changed["input"][0]["content"][0]["text"] == upstream + " (edited)"
