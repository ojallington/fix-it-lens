"""The tracked commit-msg hook takes assistant attribution lines out and leaves the rest of the message alone."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".githooks" / "commit-msg"


def _run(tmp_path: Path, message: str) -> str:
    msg = tmp_path / "MSG"
    msg.write_text(message)
    subprocess.run([str(HOOK), str(msg)], check=True)
    return msg.read_text()


def test_the_hook_is_executable() -> None:
    assert os.access(HOOK, os.X_OK)


@pytest.mark.parametrize("line", [
    "Co-Authored-By: Claude Opus <noreply@example.com>",
    "co-authored-by: claude <noreply@example.com>",
    "CO-AUTHORED-BY:Claude",
    "Claude-Session: https://example.com/s",
    "claude-session: https://example.com/s",
])
def test_attribution_lines_go_in_any_case(tmp_path: Path, line: str) -> None:
    assert _run(tmp_path, f"Subject\n\nBody.\n\n{line}\n") == "Subject\n\nBody.\n\n"


@pytest.mark.parametrize("line", [
    "Co-Authored-By: A Person <person@example.com>",
    "Signed-off-by: A Person <person@example.com>",
    "A line about Claude-Session: and Co-Authored-By: Claude, not at its start.",
])
def test_every_other_line_stays(tmp_path: Path, line: str) -> None:
    message = f"Subject\n\n{line}\n"
    assert _run(tmp_path, message) == message


def test_a_message_with_nothing_to_strip_is_unchanged_and_no_file_is_left(tmp_path: Path) -> None:
    assert _run(tmp_path, "Subject\n") == "Subject\n"
    assert [p.name for p in tmp_path.iterdir()] == ["MSG"]


def test_a_real_commit_loses_the_lines(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shutil.copy2(HOOK, hooks / "commit-msg")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        who = ["-c", "user.email=t@t", "-c", "user.name=t", "-c", f"core.hooksPath={hooks}"]
        return subprocess.run(["git", *who, *args], cwd=repo, check=True, capture_output=True, text=True).stdout

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "Subject\n\nCo-Authored-By: Claude <n@e>\nClaude-Session: https://e/s")
    assert git("log", "-1", "--format=%B").strip() == "Subject"
