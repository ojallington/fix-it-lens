"""Voice in: the transcription is biased to Dutch and English and to the card's words; a voice note outside a
job goes into the appliance's history; a failed or empty transcription asks to repeat or tap and does nothing else;
"klaar" on an instructed step starts a check; a spoken gas smell stops."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

import calls
from app import cards, voice
from app.session import StepRun
from contracts.models.safety import Stop
from tests.l1.helpers import FakeClient
from tests.l1.test_session import acme, session

WAV = b"RIFF0000WAVEfmt "


class Ears(FakeClient):
    def __init__(self, text: str | None) -> None:
        super().__init__()
        self.text = text
        self.asked: list[tuple[Any, list[str]]] = []

    async def transcribe(self, wav: bytes, bias: Any, keywords: list[str], **kw: Any) -> calls.Transcript:
        self.asked.append((bias, keywords))
        if self.text is None:
            raise calls.CallTimeout("transcribe: 6 s")
        return calls.Transcript(self.text, {}, 300.0, 0.001, "3" * 64)


class Notes:
    def __init__(self) -> None:
        self.kept: list[tuple[str, str | None]] = []

    def job_started(self, s: Any) -> None: ...

    def job_done_checked(self, s: Any) -> None: ...
    def job_done_by_person(self, s: Any) -> None: ...

    def history(self, s: Any, kind: str, text: str | None) -> None:
        self.kept.append((kind, text))


def utterance(i: int = 1) -> dict[str, Any]:
    return {"type": "utterance", "seq": i, "t_ms": 10.0 * i, "utterance_id": f"u{i}", "text": None,
            "audio_wav_b64": base64.b64encode(WAV).decode(), "lang": "nl"}


async def run(tmp_path: Path, text: str | None, *, job: bool = True, lang: str = "en") -> tuple[Any, Ears, Notes]:
    ears, notes = Ears(text), Notes()
    s = session(tmp_path, ears, hooks=notes, lang=lang)
    if not job:
        s.job_id = None
    s.start()
    await s.handle(utterance())
    await s.idle()
    await s.close()
    return s, ears, notes


async def test_bias_is_dutch_english_and_the_card_words(tmp_path: Path) -> None:
    s, ears, _ = await run(tmp_path, "waar zit het filter")
    (bias, words), = ears.asked
    assert tuple(bias) == ("Dutch", "English")
    assert words == voice.keywords(s) and "debris filter" in words and len(words) <= 100
    assert (tmp_path / "t1" / "audio" / "u1.wav").read_bytes() == WAV


def test_no_card_sends_a_keyword_the_service_refuses() -> None:
    long = "deksel van het glansspoelmiddelreservoir"
    bosch = cards.read(Path("cards/bosch/SMV4HAX40N.yaml"))
    assert long in [n for p in bosch.parts for n in p.names.root.values()]
    assert long not in voice.card_keywords(bosch)
    for path in sorted(Path("cards").glob("*/*.yaml")):
        words = voice.card_keywords(cards.read(path))
        assert words and all(len(w) <= voice.KEYWORD_MAX for w in words), path


async def test_voice_note_outside_a_job_goes_into_history(tmp_path: Path) -> None:
    s, _, notes = await run(tmp_path, "pump made a rattle on Sunday", job=False)
    assert notes.kept == [("voice_note", "pump made a rattle on Sunday")]
    assert any(m["type"] == "rail" and m["tool"] == "voice_note" for m in s.out)


async def test_failed_transcription_asks_again_and_does_nothing_else(tmp_path: Path) -> None:
    for text in (None, "   "):
        s, _, notes = await run(tmp_path / str(text is None), text, lang="nl")
        assert [m["kind"] for m in s.out if m["type"] == "notice"] == ["transcribe_failed"]
        said = " ".join(m["text"] for m in s.out if m["type"] == "sentence")
        assert said == voice.AGAIN["nl"] and notes.kept == []
        assert not any(m["type"] in ("checking", "safety") for m in s.out)


async def heard(tmp_path: Path, words: str, offered: bool = False) -> tuple[list[str], list[str | None]]:
    """What the words did on an instructed step: the checks asked for, and what the guide was handed."""
    s = session(tmp_path, Ears(words), hooks=Notes())
    s.start()
    did: list[str] = []
    guide: list[str | None] = []

    async def check(reason: str) -> None:
        did.append(reason)

    async def did_it() -> None:
        did.append("did_it")

    async def turn(text: str | None) -> None:
        guide.append(text)

    s.check, s.did_it, s._guide_turn = check, did_it, turn  # type: ignore[method-assign,assignment]
    s.phase = "instructed"
    if offered:
        s.run = StepRun(0, acme().jobs[0].steps[0], did_it_offered=True)
    await s.handle(utterance())
    await s.idle()
    await s.close()
    return did, guide


@pytest.mark.parametrize("words", ["klaar", "Done.", "yes that is done", "Ok, klaar!", "het is af", "finished"])
async def test_a_plain_done_checks_like_the_done_tap(tmp_path: Path, words: str) -> None:
    assert await heard(tmp_path, words) == (["the user said done"], [words])


@pytest.mark.parametrize("words", ["I did it", "ok I did that", "ik heb het gedaan", "Ja, heb ik gedaan."])
async def test_i_did_it_counts_like_the_did_it_tap(tmp_path: Path, words: str) -> None:
    assert await heard(tmp_path, words) == (["did_it"], [words])


async def test_done_counts_as_did_it_once_the_camera_could_not_see(tmp_path: Path) -> None:
    assert await heard(tmp_path, "yes that is done", offered=True) == (["did_it"], ["yes that is done"])


@pytest.mark.parametrize("words", ["done that but every light is red", "is it done?", "klaar, maar hij piept",
                                   "I did it but the filter won't go back", "not done yet", "ready for the next one"])
async def test_a_qualified_done_is_answered_and_counts_for_nothing(tmp_path: Path, words: str) -> None:
    assert await heard(tmp_path, words) == ([], [words])


async def test_a_spoken_gas_smell_stops(tmp_path: Path) -> None:
    s, _, _ = await run(tmp_path, "ik ruik gas")
    assert isinstance(s.safety.state, Stop) and s.phase == "stopped"


class Unheard(Ears):
    async def transcribe(self, wav: bytes, bias: Any, keywords: list[str], **kw: Any) -> calls.Transcript:
        raise calls.MissingFixture("no fixture for request")


async def test_a_replay_without_the_recording_says_no_recording_not_say_it_again(tmp_path: Path) -> None:
    s = session(tmp_path, Unheard(None))
    s.start()
    for i in range(3):
        await s.handle(utterance(i + 1))
    await s.idle()
    await s.close()
    assert [m["kind"] for m in s.out if m["type"] == "notice"] == ["no_recording"]  # once, never transcribe_failed
    assert not any(m["type"] == "sentence" for m in s.out)  # never "I didn't catch that" for a recording's gap


async def test_a_spoken_hazard_stops_at_the_household_cap(tmp_path: Path) -> None:
    """The household has spent its day: the guide is refused, but the transcription is not, so "burning smell" still
    reaches the rules and stops."""
    from datetime import UTC, datetime

    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl1 = 10.0\n")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    (private / "ledger.jsonl").write_text(json.dumps({"lane": "l1", "usd": 0.45, "household": "h1", "ts": now}) + "\n")
    ledger = calls.Ledger(private / "ledger.jsonl", private / "budgets.toml")

    class Capped(Ears):
        async def transcribe(self, wav: bytes, bias: Any, keywords: list[str], **kw: Any) -> calls.Transcript:
            ledger.check("l1", 0.0001, household=kw["household"], household_cap_usd=kw["household_cap_usd"])
            return await super().transcribe(wav, bias, keywords, **kw)

        async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
            ledger.check("l1", 0.01, household=kw["household"], household_cap_usd=kw["household_cap_usd"])
            return await super().respond(role, input, **kw)

    ears = Capped("burning smell")
    s = session(tmp_path, ears, household="h1", cap_usd=0.45, lane="l1")
    with pytest.raises(calls.BudgetExceeded):  # the cap is reached for everything else
        await ears.respond("guide", [], household="h1", household_cap_usd=0.45)
    s.start()
    await s.handle(utterance())
    await s.idle()
    await s.close()
    assert ears.asked and isinstance(s.safety.state, Stop) and s.phase == "stopped"
