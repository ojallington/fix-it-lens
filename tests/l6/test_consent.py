"""The pilot consent line the phone imports at build time (eval/consent/pilot-consent.json)."""

import json
from pathlib import Path

LINE = Path(__file__).resolve().parents[2] / "eval" / "consent" / "pilot-consent.json"
JARGON = ("store:", "store: false", "private folder", "privémap", "Standard tier", "Meta Model API", "DRAFT")


def test_line_is_in_both_languages_with_a_status_while_unsigned() -> None:
    d = json.loads(LINE.read_text())
    assert d["en"] and d["nl"] and (Path(LINE.parents[2]) / d["form"]).exists()
    if not d["signed"]:
        assert d["status"]["en"] and d["status"]["nl"]


def test_line_is_plain_words() -> None:
    d = json.loads(LINE.read_text())
    for lang in ("en", "nl"):
        assert not [j for j in JARGON if j.lower() in d[lang].lower()], lang
        assert "serial" in d["en"] and "serienummer" in d["nl"]  # the promise the form makes, kept in the line


def test_the_in_app_screen_fits_a_phone_and_asks_for_18() -> None:
    s = (LINE.parent / "in-app-consent.md").read_text()
    for lang, adult in (("Nederlands", "18 jaar of ouder"), ("English", "18 or older")):
        half = s.split(f"## {lang}\n", 1)[1].split("\n---", 1)[0]
        assert len(half.replace("**", "").replace("☐", "").split()) < 90, lang
        assert adult in half and half.count("☐") == 1 and "pilot@craftycast.com" in half


def test_the_in_app_line_is_the_in_app_form_word_for_word() -> None:
    d = json.loads((LINE.parent / "app-consent.json").read_text())
    md = (LINE.parents[2] / d["form"]).read_text()
    for lang, key in (("Nederlands", "nl"), ("English", "en")):
        half = md.split(f"## {lang}\n", 1)[1].split("\n---", 1)[0]
        paras = [" ".join(p.split()).replace("**", "").replace("☐ ", "") for p in half.strip().split("\n\n")]
        assert d[key] == "\n\n".join(paras), key
        assert not [j for j in JARGON if j.lower() in d[key].lower()], key
    if not d["signed"]:
        assert d["status"]["en"] and d["status"]["nl"]
