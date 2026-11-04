"""A session in Dutch hears Dutch: every fixed line code says or shows (the asks, the fallbacks, the pointers to the
manual, the held-step text, the notices) has a Dutch line for every English one, and each passes G0 in its own
language, so none is held where it should help."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import rules
from app import guide
from app import session as sess
from app.spoken import REST, in_lang, is_process, spoken
from tests.l1.test_session import acme, session

LANGS = ("en", "nl")


def lines() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for lang in LANGS:
        out += [(lang, t) for t in in_lang(sess.ASK_FOR, lang).values()]
        out += [(lang, t) for t in in_lang(guide.ASK_VIEW, lang).values()]
        out += [(lang, t) for t in in_lang(sess.NOTICE, lang).values()]
        out += [(lang, in_lang(t, lang)) for t in (guide.STUCK, guide.NO_ANSWER, sess.HELD_STEP, sess.GAS_AGAIN,
                                                  sess.GAS_CONFIRM)]
        out += [(lang, in_lang(REST, lang).format(page=9))]
    return out


@pytest.mark.parametrize("table", [sess.ASK_FOR, guide.ASK_VIEW, sess.NOTICE])
def test_every_english_line_has_a_dutch_one(table: dict[str, dict[str, str]]) -> None:
    assert set(table) == set(LANGS) and table["en"].keys() == table["nl"].keys()


@pytest.mark.parametrize(("lang", "line"), lines())
def test_each_line_passes_g0_in_its_language(lang: str, line: str) -> None:
    for sentence in [line] if line in sess.GAS_CONFIRM.values() else sess.split_sentences(line):
        r = rules.g0.check_sentence(sentence, lang, acme(), None, country="nl")
        assert not isinstance(r, rules.Hold), (sentence, r)


def test_a_dialect_or_an_unknown_language_falls_back() -> None:
    assert in_lang(guide.STUCK, "nl-BE") == guide.STUCK["nl"] and in_lang(guide.STUCK, "de") == guide.STUCK["en"]


def test_the_pointers_and_process_lines_in_dutch() -> None:
    table = "Het hangt af van je water.\n\n" + "\n".join(f"* {a} °dH: H0{i}" for i, a in enumerate(range(0, 10, 5)))
    assert spoken(table, page=9, lang="nl") == "Het hangt af van je water. 0 °dH: H00. 5 °dH: H01."
    long = "Vul hem tot de rand. Gebruik alleen vaatwasserzout. Nooit keukenzout. Veeg gemorst zout weg."
    assert spoken(long, page=7, lang="nl").endswith("De rest staat in de handleiding op pagina 7.")
    for line in ("Laat me het citaat even goed zetten.", "Even de handleiding nakijken.",
                 "Ik zoek het op in de handleiding."):
        assert is_process(line), line
    for line in ("Laat me even weten als het gelukt is.", "Kijk even in de handleiding op pagina 4.",
                 "Ik zie het filter onderin."):
        assert not is_process(line), line


async def test_a_dutch_session_asks_in_dutch(tmp_path: Path) -> None:
    s = session(tmp_path, lang="nl")
    s.start()
    await s.start_job()
    await s.idle()
    said: list[Any] = [m["text"] for m in s.out if m["type"] == "sentence"]  # type: ignore[attr-defined]
    asks = set(sess.ASK_FOR["nl"].values())
    assert any(t in asks for t in said) and not set(sess.ASK_FOR["en"].values()) & set(said), said
    await s.close()


def test_dutch_lines_name_the_buttons_as_the_phone_labels_them() -> None:
    import re

    named = [m.group(1) for lang, line in lines() if lang == "nl" for m in re.finditer(r"\b[Tt]ik op (\w+)", line)]
    assert named and set(named) <= {"Ja", "Klaar", "Verder"}, named
