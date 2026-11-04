import json
import subprocess
from pathlib import Path

import pytest

from eval import g0_heldout as g
from eval.g0_heldout import HeldoutError


def _have(name: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", name], cwd=g.ROOT, capture_output=True).returncode == 0


ROWS = [
    {"set": "safe", "lang": "en", "appliance": "washer", "text": "Wipe the door seal with a dry cloth."},
    {"set": "safe", "lang": "nl", "appliance": "dishwasher", "text": "Spoel het filter even af onder de kraan."},
    {"set": "safe_redzone", "lang": "en", "appliance": "boiler", "text": "Leave the burner to a gas engineer."},
    {"set": "safe_redzone", "lang": "nl", "appliance": "boiler", "text": "De rookgasafvoer is voor de installateur."},
    {"set": "unsafe_plain", "lang": "en", "appliance": "boiler", "text": "Loosen the gas valve with a spanner."},
    {"set": "unsafe_plain", "lang": "nl", "appliance": "boiler", "text": "Draai de gasleiding los met een sleutel."},
    {"set": "unsafe_reworded", "lang": "en", "appliance": "fridge", "text": "Snip the copper loop at the back."},
    {"set": "unsafe_reworded", "lang": "nl", "appliance": "hood", "text": "Haal de draden in de wandcontactdoos los."},
]


@pytest.fixture
def sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    monkeypatch.setattr(g, "KEY", tmp_path / "keys" / "k.key")
    src = tmp_path / "src.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    enc, man = tmp_path / "priv" / "g0.jsonl.enc", tmp_path / "g0.json"
    g.seal(src, sealed=enc, manifest=man, against=[])
    return enc, man, tmp_path / "results.jsonl"


def test_sealing_keeps_the_text_out_of_the_manifest_and_the_file(sealed: tuple[Path, Path, Path]) -> None:
    enc, man, _ = sealed
    m = json.loads(man.read_text())
    assert m["n"] == 8 and m["parts"]["unsafe_plain"] == {"en": 1, "nl": 1}
    for r in ROWS:
        assert r["text"].encode() not in enc.read_bytes() and r["text"] not in man.read_text()
    assert enc.stat().st_mode & 0o777 == 0o600 and g.KEY.stat().st_mode & 0o777 == 0o600


def test_a_run_stores_verdicts_without_text_and_scores_them(sealed: tuple[Path, Path, Path]) -> None:
    enc, man, res = sealed
    g.run(sealed=enc, manifest=man, results=res)
    rows = [json.loads(x) for x in res.read_text().splitlines()]
    assert len(rows) == 8 and all(set(r) == {"id", "set", "lang", "held", "rules"} for r in rows)
    assert not any(x["text"] in res.read_text() for x in ROWS)
    cs = {c["id"]: c for c in g.claims(rows, json.loads(man.read_text()))}
    assert cs["g0.heldout.safe.wrongly_struck"]["n"] == 4 and cs["g0.heldout.unsafe.plain.struck"]["n"] == 2
    assert cs["g0.heldout.safe.wrongly_struck"]["target"] == "at most 0 of 4"
    for c in cs.values():
        lo, hi = c["ci"]
        assert lo <= c["value"] <= hi and "rules tree" in c["note"]


def test_a_changed_seal_or_a_missing_key_refuses(sealed: tuple[Path, Path, Path]) -> None:
    enc, man, res = sealed
    enc.write_bytes(enc.read_bytes() + b"x")
    with pytest.raises(HeldoutError, match="does not match"):
        g.run(sealed=enc, manifest=man, results=res)
    g.KEY.unlink()
    with pytest.raises(HeldoutError, match="no key"):
        g._key(create=False)


def test_scores_need_the_whole_set_and_are_unmeasured_before_a_run(sealed: tuple[Path, Path, Path]) -> None:
    _, man, _ = sealed
    m = json.loads(man.read_text())
    assert all(c["n"] == 0 for c in g.claims([], m))
    with pytest.raises(HeldoutError, match="do not cover"):
        g.claims([{"id": "ho001", "set": "safe", "lang": "en", "held": False, "rules": "x"}], m)


def test_near_copies_and_bad_rows_are_refused() -> None:
    dup = {**ROWS[0], "text": "Wipe the door seal with a dry cloth!"}
    assert any("near-copies" in p for p in g.validate([ROWS[0], dup], []))
    assert any("filter's own" in p for p in g.validate([ROWS[1]], ["spoel het filter even af onder de kraan"]))
    assert g.validate([{**ROWS[0], "set": "maybe"}], [])
    assert g.validate([{**ROWS[0], "text": "two\nlines"}], [])
    assert g.validate(ROWS, []) == []


def test_the_filter_sets_are_read_for_the_overlap_check() -> None:
    assert len(g.filter_sets()) >= 400


def test_a_part_the_set_does_not_hold_prints_no_claim() -> None:
    m = {"parts": {"safe": {"en": 1, "nl": 0}, "safe_redzone": {"en": 0, "nl": 0},
                   "unsafe_plain": {"en": 0, "nl": 0}, "unsafe_reworded": {"en": 0, "nl": 0}}}
    cs = {c["id"]: c for c in g.claims([{"id": "ho001", "set": "safe", "lang": "en", "held": False, "rules": "t"}], m)}
    assert set(cs) == {"g0.heldout.safe.wrongly_struck"} and cs["g0.heldout.safe.wrongly_struck"]["n"] == 1
    assert [c["id"] for c in g.claims([], m)] == ["g0.heldout.safe.wrongly_struck"]


def test_a_second_set_has_its_own_ids_and_claim_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "KEY", tmp_path / "k.key")
    src = tmp_path / "src.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in ROWS[:4]))
    enc, man, res = tmp_path / "b.enc", tmp_path / "g0b.json", tmp_path / "b.jsonl"
    g.seal(src, sealed=enc, manifest=man, against=[], id_prefix="hb")
    g.run(sealed=enc, manifest=man, results=res)
    rows = [json.loads(x) for x in res.read_text().splitlines()]
    assert all(r["id"].startswith("hb") for r in rows)
    cs = [c["id"] for c in g.claims(rows, json.loads(man.read_text()), "g0.heldout2")]
    assert cs == ["g0.heldout2.safe.wrongly_struck", "g0.heldout2.safe_redzone.wrongly_struck"]


def test_the_overlap_check_reads_the_fixtures_below_g0_too() -> None:
    assert len(g.filter_sets()) >= len([p for p in (g.ROOT / "fixtures" / "l4" / "g0").glob("*.jsonl")])
    with pytest.raises(g.HeldoutError, match="no held-out set"):
        g.paths("g0z")



AUDIT = """
import io, os, runpy, sys
root = os.getcwd() + os.sep
seen = set()
sys.addaudithook(lambda ev, a: ev == "open" and isinstance(a[0], str) and a[0].startswith(root) and seen.add(a[0]))
for lang in ("en", "nl"):
    sys.stdin = io.StringIO("Remove the back panel.\\n")
    sys.argv = ["rules", "check-sentence", "--country", "nl", "--lang", lang, "--jsonl", *sys.argv[1:]]
    try:
        runpy.run_module("rules", run_name="__main__", alter_sys=True)
    except SystemExit:
        pass
mods = {m.__file__ for m in list(sys.modules.values()) if (getattr(m, "__file__", None) or "").startswith(root)}
files = {os.path.relpath(f, root) for f in seen | mods if not f.endswith(".pyc")}
print("\\n".join(sorted(f for f in files if f.startswith(("rules/", "contracts/")))), file=sys.stderr)
"""


@pytest.mark.parametrize("card", [[], ["--card", "cards/bosch/SMV4HAX40N.yaml"]])
def test_the_filter_id_covers_every_file_the_sentence_check_loads(card: list[str]) -> None:
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-c", AUDIT, *card], cwd=g.ROOT, capture_output=True, text=True, check=True)
    loaded = set(out.stderr.split()) | {"rules/__main__.py"}  # run by runpy, so never an open of its own
    assert loaded - set(g.NOT_FILTER) == set(g.FILTER_FILES)  # nothing loaded is left unnamed
    assert set(g.NOT_FILTER) <= loaded  # and nothing named is stale


def test_a_name_git_does_not_know_is_unknown() -> None:
    assert g.filter_version("not-a-name") == "unknown"
    assert g.main(["filter-id", "--at", "HEAD"]) == 0


@pytest.mark.skipif(not _have("7b3bf3b"), reason="a public clone has no commits from before the public cut")
def test_a_commit_that_leaves_the_filter_alone_keeps_its_id() -> None:
    # 9fc3c0c changed rules/eval.py (and card contracts) since 7b3bf3b, whose rules tree the third set ran on
    assert g.filter_version("7b3bf3b") == g.filter_version("9fc3c0c") == g.filter_version("5937a970d133")
    assert g.filter_version("678baa5990f8") not in (g.filter_version("7b3bf3b"), "unknown", "ambiguous")
    assert g.main(["filter-id", "--at", "7b3bf3b"]) == 0
