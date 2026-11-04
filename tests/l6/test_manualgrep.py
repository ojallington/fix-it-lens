import json
from pathlib import Path

import pytest

from eval.manualgrep import SHINGLE, scan, shingles

PAGE = ("Draai de grove zeef tegen de wijzers van de klok in en til het zeefsysteem eruit. "
        "Let op dat er geen voorwerpen in het pompreservoir vallen.")


def _index() -> dict[str, str]:
    return {s: "bosch/X.pdf p42" for s in shingles(PAGE)}


def test_copied_sentence_is_caught_even_rewrapped(tmp_path: Path) -> None:
    f = tmp_path / "fixtures" / "l1" / "a.jsonl"
    f.parent.mkdir(parents=True)
    copied = "Ok: draai de grove zeef tegen de\n  wijzers van de KLOK in en til"
    f.write_text(json.dumps({"fixture": 1}) + "\n" + json.dumps({"event": {"delta": copied}}) + "\n")
    hits = scan([tmp_path / "fixtures"], _index(), tmp_path)
    assert len(hits) == 1 and hits[0].line == 2 and "p42" in str(hits[0])


def test_short_overlap_passes(tmp_path: Path) -> None:
    f = tmp_path / "r.jsonl"
    short = PAGE[:SHINGLE - 1]
    f.write_text(json.dumps({"evidence": f"I see: {short}"}) + "\n")
    assert scan([f], _index(), tmp_path) == []


def test_escaped_unicode_in_json_is_decoded(tmp_path: Path) -> None:
    f = tmp_path / "r.jsonl"
    f.write_text(json.dumps({"t": PAGE[10:80]}, ensure_ascii=True) + "\n")
    assert len(scan([f], _index(), tmp_path)) == 1


def test_main_exits_nonzero_on_a_real_hit_and_skips_acme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import eval.manualgrep as mg

    (tmp_path / "manuals" / "bosch").mkdir(parents=True)
    (tmp_path / "manuals" / "acme").mkdir(parents=True)
    (tmp_path / "manuals" / "bosch" / "X.pdf").write_bytes(b"%PDF")
    (tmp_path / "manuals" / "acme" / "WM-0000.pdf").write_bytes(b"%PDF")
    acme = "Unplug the machine from the socket before opening the filter flap at the bottom right."
    texts = {"X.pdf": PAGE, "WM-0000.pdf": acme}

    class P:
        def __init__(self, t: str) -> None:
            self.text, self.number = t, 1

    import ingest.pages
    monkeypatch.setattr(ingest.pages, "load", lambda pdf: [P(texts[pdf.name])])
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path))
    rec = tmp_path / "rec.jsonl"
    rec.write_text(json.dumps({"t": acme}) + "\n")
    assert mg.main([str(rec)]) == 0  # the Acme dummy is public by design
    rec.write_text(json.dumps({"t": PAGE[5:70]}) + "\n")
    assert mg.main([str(rec)]) == 1


def _manuals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, texts: dict[str, str]) -> Path:
    """Fake manuals at manuals/<maker>/<file>.pdf with the given page text."""
    import ingest.pages

    for rel in texts:
        (tmp_path / "manuals" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "manuals" / rel).write_bytes(b"%PDF")

    class P:
        def __init__(self, t: str) -> None:
            self.text, self.number = t, 1

    monkeypatch.setattr(ingest.pages, "load",
                        lambda pdf: [P(texts[str(pdf.relative_to(tmp_path / "manuals"))])])
    return tmp_path / "manuals"


GENERIC = "Trek altijd de stekker uit het stopcontact voordat u het apparaat reinigt."
SHARED = "trek altijd de stekker uit het stopcontact voordat u"  # 51 characters: past one shingle


def test_a_phrase_in_two_makers_manuals_is_generic_but_one_makers_own_text_is_not(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import eval.manualgrep as mg

    manuals = _manuals(tmp_path, monkeypatch, {
        "aeg/LR.pdf": f"{GENERIC} {PAGE}",
        "siemens/WM.pdf": "Belangrijk: trek altijd de stekker uit het stopcontact voordat u begint.",
    })
    index = mg.manual_index(manuals)
    rec = tmp_path / "rec.jsonl"
    rec.write_text(json.dumps({"t": SHARED}) + "\n"
                   + json.dumps({"t": PAGE[5:70]}) + "\n")
    hits = scan([rec], index, tmp_path)
    assert [h.line for h in hits] == [2] and "aeg/LR.pdf" in hits[0].manual


def test_two_manuals_of_one_maker_never_make_a_phrase_generic(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    import eval.manualgrep as mg

    manuals = _manuals(tmp_path, monkeypatch, {"bosch/A.pdf": GENERIC, "bosch/B.pdf": GENERIC})
    rec = tmp_path / "rec.jsonl"
    rec.write_text(json.dumps({"t": SHARED}) + "\n")
    assert len(scan([rec], mg.manual_index(manuals), tmp_path)) == 1
