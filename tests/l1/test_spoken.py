"""The guide's text as the phone draws and speaks it (app.spoken, polish finding 4): no Markdown, no lines about its
own tools or citations, no quote read out, at most two sentences of prose, and a list's steps always said, each
through G0 on its own, never replaced by a page to look up. The texts are written here in the shapes the live guide
used; none is manual text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ask import answer
from app.guide import NO_ANSWER
from app.spoken import MAX_ITEMS, is_process, plain, split, spoken
from eval.spoken import has_markdown
from tests.l1.test_ask import Manual
from tests.l1.test_guide import Scripted, guided, resp
from tests.l1.test_session import HELD, acme

MARKS = set("*_#>`|")


@pytest.mark.parametrize(("md", "out"), [
    ("Turn the filter **anticlockwise** and lift it *out*.", "Turn the filter anticlockwise and lift it out."),
    ("## Cleaning\nRinse it under the `tap`.", "Cleaning. Rinse it under the tap."),
    ("Two things:\n\n* rinse the filter\n* refit it", "Rinse the filter. Refit it."),
    ("Before you start, two things:\n\n* rinse it\n* dry it", "Before you start, two things. Rinse it. Dry it."),
    ("Use [the brush](https://example.com) gently.", "Use the brush gently."),
    ("Rinse it.\n\n> “Spoel het filter onder stromend water af.”\n\nThen refit it.", "Rinse it. Then refit it."),
    ("Keep snake_case_words and 3*4 as they are.", "Keep snake_case_words and 3*4 as they are."),
])
def test_plain_strips_markdown(md: str, out: str) -> None:
    assert plain(md) == out and not has_markdown(out)  # eval's definition of Markdown, so both lanes use one


# the shape of the live reply to "What do I actually need to do to clean the filter?": the steps as a numbered list
# with a nested one, then the next thing to do with an "e.g." in it. Code once said "the full table is on page 43"
# in place of the list and cut the last sentence at "e.g.".
STEPS = ("The insert is already out, good for cleaning. Here's what to do:\n\n"
         "1. **The filter system is out** — normally you turn the coarse filter anticlockwise and lift it out.\n"
         "2. **Take it apart:**\n   * pull the flat fine filter off downwards,\n"
         "   * press the clips together and lift the coarse filter out.\n"
         "3. **Rinse every part** under running water.\n4. **Put it together** until the tabs click.\n"
         "5. **Refit it** and turn the coarse filter clockwise until the arrows meet.\n\n"
         "Once it's back in, put the care product in — e.g. the bottle hung in the basket — and tell me.")
SAID = ("The insert is already out, good for cleaning. Here's what to do. The filter system is out — normally you turn"
        " the coarse filter anticlockwise and lift it out. Take it apart. Pull the flat fine filter off downwards."
        " Press the clips together and lift the coarse filter out. Rinse every part under running water. Put it"
        " together until the tabs click. Refit it and turn the coarse filter clockwise until the arrows meet. Once"
        " it's back in, put the care product in — e.g. the bottle hung in the basket — and tell me.")


def test_the_steps_are_said_and_never_replaced_by_a_page() -> None:
    assert spoken(STEPS, page=43) == SAID and "page" not in SAID


def test_a_table_is_read_row_by_row() -> None:
    table = "It depends on your water.\n\nFrom the manual, page 9:\n\n" + "\n".join(
        f"* **{a} - {a + 2} °dH** -> **H0{i}**" for i, a in enumerate(range(0, 15, 5)))
    assert spoken(table) == "It depends on your water. 0 - 2 °dH: H00. 5 - 7 °dH: H01. 10 - 12 °dH: H02."
    piped = "| hardness | value |\n|---|---|\n" + "\n".join(f"| {a} | H0{a} |" for a in range(2))
    assert spoken(piped, page=12) == "Hardness — value. 0 — H00. 1 — H01."


def test_a_list_past_max_items_is_pointed_to_after_what_was_said() -> None:
    text = "Here is the list:\n\n" + "\n".join(f"* item {i}" for i in range(MAX_ITEMS + 2))
    said = spoken(text, page=7)
    assert said.startswith("Here is the list. Item 0.") and f"Item {MAX_ITEMS - 1}." in said
    assert f"Item {MAX_ITEMS}." not in said and said.endswith("The manual has the rest on page 7.")


def test_the_list_does_not_raise_the_cap_on_prose() -> None:
    text = "One. Two. Three.\n\n* a\n* b\n\nFour. Five."
    assert spoken(text) == "One. Two. A. B."  # two sentences of prose in all, the items beside them
    assert spoken("One. Two. Three. Four.") == "One. Two."


@pytest.mark.parametrize(("text", "out"), [
    ("Hang it in the basket, e.g. at the back. Then close it.", ["Hang it in the basket, e.g. at the back.",
                                                                 "Then close it."]),
    ("Use a mild one, i.e. no bleach.", ["Use a mild one, i.e. no bleach."]),
    ("Gebruik een doekje, bijv. een oude theedoek. Klaar.", ["Gebruik een doekje, bijv. een oude theedoek.",
                                                            "Klaar."]),
    ("It is on p. 43 of the manual. Done.", ["It is on p. 43 of the manual.", "Done."]),
])
def test_an_abbreviation_does_not_end_a_sentence(text: str, out: list[str]) -> None:
    assert split(text) == out


async def test_each_item_goes_through_g0_on_its_own(tmp_path: Path) -> None:
    s, _ = guided(tmp_path, [resp(f"Three things:\n\n* rinse the filter\n* {HELD}\n* refit the filter")])
    s.start()
    await s.guide.turn("what now?")  # type: ignore[union-attr]
    said = [(m["type"], m.get("text") or m.get("rule_id")) for m in s.out if m["type"] in ("sentence", "held")]
    assert said[0] == ("sentence", "Rinse the filter.") and said[1][0] == "held"
    assert ("sentence", "Refit the filter.") in said[2:]  # the held item is replaced; the rest still play
    assert HELD not in json.dumps(s.out)
    await s.close()


@pytest.mark.parametrize("line", [
    "Let me fix that manual citation.", "That citation needs a correction — I'll retry it.",
    "What the salt does — checking the manual.", "I'll check the manual for suitable detergents.",
    "Let me pull the exact manual wording for that step.", "That citation didn't go through, so I'll retry it now.",
    "Where it goes — I'll point you to the reservoir inside the door.", "I'll call lookup_manual first.",
])
def test_process_lines_are_never_said(line: str) -> None:
    assert is_process(line) and spoken(line) == ""


@pytest.mark.parametrize("line", [
    "Let me know once you've done that.", "Turn it until it clicks.", "The filter sits under the lower spray arm.",
])
def test_ordinary_lines_are_not_process_lines(line: str) -> None:
    assert not is_process(line)


def test_a_quote_is_shown_by_cite_not_read_out() -> None:
    assert spoken("Check the filter after every wash.\n\nManual says: *“Na elke beurt het filter nakijken.”*") == \
        "Check the filter after every wash."


def test_a_long_reply_is_cut_to_two_sentences_and_the_page() -> None:
    text = "Fill it to the top. Use only dishwasher salt. Never use table salt. Wipe off any spill. Close the cap."
    assert spoken(text, page=7) == "Fill it to the top. Use only dishwasher salt. The manual has the rest on page 7."
    assert spoken(text) == "Fill it to the top. Use only dishwasher salt."


async def test_text_beside_a_tool_call_is_not_said_when_the_answer_follows() -> None:
    step = acme().jobs[0].steps[0]
    c = Scripted([resp("Let me get that citation right.", calls_=[("cite", {"step_id": step.id, "page": None,
                                                                             "quote": None})]),
                  resp("**Every two months.**\n\n* turn it **anticlockwise")])
    out = await answer(acme(), "How often?", manuals=Manual(), client=c)  # type: ignore[arg-type]
    assert out["text"] == "Every two months. Turn it anticlockwise."
    assert not MARKS & set(out["text"]) and not has_markdown(out["text"])


async def test_text_beside_a_tool_call_is_said_when_nothing_else_is() -> None:
    step = acme().jobs[0].steps[0]
    cite = ("cite", {"step_id": step.id, "page": None, "quote": None})
    c = Scripted([resp("Every two months.", calls_=[cite]), resp("")])
    out = await answer(acme(), "How often?", manuals=Manual(), client=c)  # type: ignore[arg-type]
    assert out["text"] == "Every two months."
    c = Scripted([resp("Let me fix that citation.", calls_=[cite]), resp("")])
    out = await answer(acme(), "How often?", manuals=Manual(), client=c)  # type: ignore[arg-type]
    assert out["text"] == NO_ANSWER["en"]
