"""fixit ingest --pr: check on the same PDF, then a branch with exactly the card and its record, and a PR body with no
manual text."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ingest import appliance_file as af
from ingest import pr
from ingest.cards import write_card
from tests.l3.test_appliance_file import DRAFT
from tests.l3.test_flow import _client


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n")
    (r / "notes.txt").write_text("not part of the card\n")
    _git(r, "add", "README.md")
    _git(r, "commit", "-q", "-m", "init")
    return r


async def _built(tmp_path: Path, acme_pdf: Path) -> af.IngestResult:
    good = copy.deepcopy(DRAFT)
    good["jobs"] = good["jobs"][:1]
    client = _client(tmp_path / "fx", acme_pdf.read_bytes(), good)
    pdf = tmp_path / "WM-0000.pdf"
    pdf.write_bytes(acme_pdf.read_bytes())
    res = await af.build(pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme", client=client)
    assert res.card is not None, res.error
    return res


async def test_branch_holds_exactly_the_card_and_its_record(tmp_path: Path, acme_pdf: Path, repo: Path) -> None:
    res = await _built(tmp_path, acme_pdf)
    card = write_card(res.card, repo / "cards")  # type: ignore[arg-type]
    assert pr.open_pr(card, tmp_path / "WM-0000.pdf", res, push=False) == "card/acme-wm-0000"
    assert _git(repo, "branch", "--show-current") == "card/acme-wm-0000"
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == [
        "cards/acme/WM-0000.check.json", "cards/acme/WM-0000.yaml"]
    assert "notes.txt" in _git(repo, "status", "--short")  # left alone


async def test_pr_body_has_counts_and_no_manual_text(tmp_path: Path, acme_pdf: Path, repo: Path) -> None:
    res = await _built(tmp_path, acme_pdf)
    card = write_card(res.card, repo / "cards")  # type: ignore[arg-type]
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kw))
        if argv[:1] == ["gh"] or "push" in argv:
            return subprocess.CompletedProcess(argv, 0, "https://example.invalid/pr/1\n", "")
        return subprocess.run(argv, **kw)

    assert pr.open_pr(card, tmp_path / "WM-0000.pdf", res, run=run) == "https://example.invalid/pr/1"
    gh = [kw for argv, kw in calls if argv[:1] == ["gh"]]
    body = gh[0]["input"]
    assert res.rail in body and "all passed" in body
    quotes = [s["quote"]["text"] for j in res.card["jobs"] for s in j["steps"]]  # type: ignore[index]
    assert not any(q in body for q in quotes)


async def test_a_card_that_fails_the_check_is_never_proposed(tmp_path: Path, acme_pdf: Path, repo: Path) -> None:
    res = await _built(tmp_path, acme_pdf)
    res.card["manual"]["page_text_sha256"][0] = "0" * 64  # type: ignore[index]
    card = write_card(res.card, repo / "cards")  # type: ignore[arg-type]
    with pytest.raises(pr.PrError, match="fails make check-pr"):
        pr.open_pr(card, tmp_path / "WM-0000.pdf", res, push=False)
    assert _git(repo, "branch", "--show-current") == "main"
