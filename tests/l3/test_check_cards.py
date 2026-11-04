"""make check-pr's rules step (tools/check_cards.py) and fixit ingest --pr: a refused job is named with its rule and
reason, so a pass with refused jobs never reads as a clean pass; a card refused as a whole fails and is never
proposed."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from ingest import appliance_file as af
from ingest import pr
from ingest.cards import write_card
from tests.l1.test_session import HELD
from tests.l3.test_appliance_file import DRAFT
from tests.l3.test_flow import _client
from tests.l3.test_pr import repo  # noqa: F401  (fixture)
from tools import check_cards


async def _built(tmp_path: Path, acme_pdf: Path) -> af.IngestResult:
    """The one-job Acme card, with its job copied under a second id so one job can be refused alone."""
    good = copy.deepcopy(DRAFT)
    good["jobs"] = good["jobs"][:1]
    client = _client(tmp_path / "fx", acme_pdf.read_bytes(), good)
    pdf = tmp_path / "WM-0000.pdf"
    pdf.write_bytes(acme_pdf.read_bytes())
    res = await af.build(pdf, brand="acme", model="WM-0000", source_url="https://example.invalid/acme", client=client)
    assert res.card is not None, res.error
    twin = copy.deepcopy(res.card["jobs"][0])
    twin["id"] = "again"
    for step in twin["steps"]:
        step["id"] = f"again-{step['id']}"
    res.card["jobs"].append(twin)
    return res


def _hold_second_job(card: dict[str, Any]) -> str:
    job = card["jobs"][1]
    job["steps"][0]["text"]["en"] = HELD
    return str(job["id"])


async def test_a_held_job_is_named_and_the_card_still_passes(tmp_path: Path, acme_pdf: Path) -> None:
    res = await _built(tmp_path, acme_pdf)
    held = _hold_second_job(res.card)  # type: ignore[arg-type]
    card = write_card(res.card, tmp_path / "cards")  # type: ignore[arg-type]
    got = check_cards.check(card, "nl")
    assert got["status"] == "pass" and list(got["refused"]) == [held]
    shown = check_cards.line(got)
    assert "pass, 1 job refused" in shown and f"refused {held}:" in shown and "held by nl." in shown
    assert check_cards.main([str(card)]) == 0


async def test_a_red_zone_below_the_floor_refuses_the_card(tmp_path: Path, acme_pdf: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    res = await _built(tmp_path, acme_pdf)
    res.card["red_zones"][0]["rule_id"] = "nl.mains.no_such_rule"  # type: ignore[index]
    card = write_card(res.card, tmp_path / "cards")  # type: ignore[arg-type]
    assert check_cards.main([str(card), "--json"]) == 1
    got = json.loads(capsys.readouterr().out)[0]
    assert got["status"] == "fail" and any("has no rule" in f for f in got["failures"])


async def test_the_pr_body_lists_each_refused_job(tmp_path: Path, acme_pdf: Path, repo: Path) -> None:  # noqa: F811
    res = await _built(tmp_path, acme_pdf)
    held = _hold_second_job(res.card)  # type: ignore[arg-type]
    card = write_card(res.card, repo / "cards")  # type: ignore[arg-type]
    bodies: list[str] = []

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if argv[:1] == ["gh"] or "push" in argv:
            bodies.append(kw.get("input") or "")
            return subprocess.CompletedProcess(argv, 0, "https://example.invalid/pr/1\n", "")
        return subprocess.run(argv, **kw)

    assert pr.open_pr(card, tmp_path / "WM-0000.pdf", res, run=run) == "https://example.invalid/pr/1"
    body = bodies[-1]
    assert "Refused by the rules check (nl)" in body and f"- `{held}`:" in body and "nl.mains" not in body.split(
        "Refused")[0]


async def test_a_card_refused_as_a_whole_is_never_proposed(tmp_path: Path, acme_pdf: Path, repo: Path) -> None:  # noqa: F811
    res = await _built(tmp_path, acme_pdf)
    res.card["red_zones"][0]["rule_id"] = "nl.mains.no_such_rule"  # type: ignore[index]
    card = write_card(res.card, repo / "cards")  # type: ignore[arg-type]
    with pytest.raises(pr.PrError, match="fails the rules check"):
        pr.open_pr(card, tmp_path / "WM-0000.pdf", res, push=False)
    assert not (card.parent / "WM-0000.check.json").exists()
    assert yaml.safe_load(card.read_text())["red_zones"][0]["rule_id"] == "nl.mains.no_such_rule"
