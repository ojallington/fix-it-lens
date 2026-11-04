import json
import shutil
from pathlib import Path

import pytest

from eval.cardcount import claims
from ingest.cards import CARDS

CARD = CARDS / "bosch" / "SMV4HAX40N.yaml"


def test_the_counts_come_from_the_card_and_its_record() -> None:
    c = {x["id"]: x for x in claims(CARD, "bosch")}
    assert c["card.bosch.quotes_checked"]["value"] <= c["card.bosch.quotes_checked"]["n"]
    assert c["card.bosch.pages"]["value"] == len(json.loads(CARD.with_suffix(".check.json").read_text())
                                                 ["page_text_sha256"])
    assert all(x["n"] > 0 and x["ci"] is None for x in c.values())


def test_a_card_edited_after_its_check_fails_instead_of_counting(tmp_path: Path) -> None:
    card = tmp_path / "SMV4HAX40N.yaml"
    shutil.copy(CARD, card)
    shutil.copy(CARD.with_suffix(".check.json"), tmp_path / "SMV4HAX40N.check.json")
    card.write_text(card.read_text() + "\n# edited\n")
    with pytest.raises(ValueError, match="not current"):
        claims(card, "bosch")
