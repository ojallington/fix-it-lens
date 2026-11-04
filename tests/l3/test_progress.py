"""The wait screen's fields are code's own: the manual's language from its words, the bar never going back."""

from __future__ import annotations

import json

from ingest import progress as pg


def test_a_page_language_from_its_small_words() -> None:
    assert pg.page_lang("Open the door and remove the filter. Clean it with water and put it back in the machine; "
                        "it is not to be used with the power on, or the door of the drum.") == "en"
    assert pg.page_lang("Open de deur en haal het filter eruit. Spoel het af met water en zet het terug in de "
                        "machine; het is niet voor gebruik met de stroom aan, of de deur van de trommel.") == "nl"
    assert pg.page_lang("WM-0000 12 34") is None


def test_a_multilingual_manual_counts_as_the_households_language_when_it_has_its_pages() -> None:
    en = "the door and the filter is not to be used with the power on, or the drum of this machine " * 2
    nl = "de deur en het filter is niet voor gebruik met de stroom aan, of de trommel van een machine " * 2
    texts = [en] * 8 + [nl] * 3
    assert pg.manual_lang(texts) == "en"
    assert pg.manual_lang(texts, prefer="nl") == "nl"
    assert pg.manual_lang([en] * 30 + [nl], prefer="nl") == "en"


def test_progress_never_goes_backwards_and_eta_counts_down() -> None:
    now = [0.0]
    seen: list[dict[str, object]] = []
    t = pg.Tracker(seen.append, clock=lambda: now[0])
    t.update(0.5)
    t.update(0.3)
    assert seen[-1]["progress"] == 0.5
    t.total_s = 100.0
    now[0] = 40.0
    t.update()
    assert (seen[-1]["elapsed_s"], seen[-1]["eta_s"]) == (40, 60)
    now[0] = 140.0
    t.update()
    assert seen[-1]["eta_s"] == 0


DRAFT_TEXT = json.dumps({
    "appliance_kind": "washer", "manual_lang": "nl", "title": "a {jobs} [title]", "doc_id": "x",
    "parts": [{"id": "jobs", "name": "\"}]{"}], "red_zones": [],
    "jobs": [{"id": "a", "steps": [{"quote": "Draai {de} kraan] dicht.\\\""}]}, {"id": "b", "steps": []}],
    "keywords": ["jobs"],
})


def test_each_job_comes_out_of_the_stream_as_soon_as_it_closes() -> None:
    s = pg.JobStream()
    got: list[tuple[int, str]] = []
    for i, ch in enumerate(DRAFT_TEXT):  # one character per delta: the worst a stream can do
        got += [(i, j["id"]) for j in s.feed(ch)]
    assert [g[1] for g in got] == ["a", "b"]
    assert got[0][0] < DRAFT_TEXT.index('"b"')  # job a before job b is written
    assert s.top == {"appliance_kind": "washer", "manual_lang": "nl", "title": "a {jobs} [title]", "doc_id": "x"}


def test_a_retried_reply_starts_the_scan_again() -> None:
    s = pg.JobStream()
    half = DRAFT_TEXT[:DRAFT_TEXT.index('"b"')]
    assert [j["id"] for j in s.feed(half, 1)] == ["a"]
    assert [j["id"] for j in s.feed(DRAFT_TEXT, 2)] == ["a", "b"]
