"""Voice in. Hold to talk: the phone sends the utterance as a WAV; muse-voice-transcribe-1.0 transcribes it, biased
to Dutch and English and to the card's keywords. The app keeps the words; it never claims to hear the sound.
A failed transcription asks the user to repeat or tap."""

from __future__ import annotations

from typing import TYPE_CHECKING

import calls
from app import trace
from contracts.models import ws
from contracts.models.card import Card

if TYPE_CHECKING:
    from app.session import Session


AGAIN = {"en": "I didn't get that. Say it again, or use the buttons.",
         "nl": "Dat verstond ik niet. Zeg het nog eens, of gebruik de knoppen."}


def keywords(s: Session) -> list[str]:
    return card_keywords(s.card) if s.card is not None else []


KEYWORD_MAX = 30  # the transcription service refuses the whole call (HTTP 400) when one keyword is longer


def card_keywords(card: Card) -> list[str]:
    """The card's keywords and its parts' names, the speech bias a session sends (at most 100, none longer than the
    service takes: a long part name is left out of the bias, not cut, so the words sent are still the card's)."""
    words = [k.root for k in card.keywords]
    for p in card.parts:
        words += [n for n in p.names.root.values() if n not in words]
    return [w for w in words if len(w) <= KEYWORD_MAX][:100]


async def utterance_text(s: Session, msg: ws.Utterance, wav: bytes | None) -> str | None:
    if msg.text:
        s.rec.state(s.clock(), "transcript", utterance_id=msg.utterance_id, source="typed", text=msg.text)
        return msg.text
    if not wav or s.mode == "capture":
        return None
    try:
        t, ms = await s.models.transcribe(wav, keywords(s))
    except (calls.CallError, ValueError) as e:
        await s.rail("transcribe", "", trace.failed(e) if isinstance(e, calls.CallError) else "couldn't hear it", 0,
                     utterance_id=msg.utterance_id, error=type(e).__name__)
        if isinstance(e, calls.MissingFixture):
            return None  # a replay without this recording: the no_recording notice said so; the user said nothing wrong
        await s.notice("transcribe_failed")
        await s.say(AGAIN.get(s.lang, AGAIN["en"]))
        return None
    text = t.text.strip()
    s.rec.state(s.clock(), "transcript", utterance_id=msg.utterance_id, source="voice", text=text)
    await s.rail("transcribe", "", f"“{text[:60]}”" if text else "nothing heard", ms, utterance_id=msg.utterance_id)
    if not text:
        await s.notice("transcribe_failed")
        await s.say(AGAIN.get(s.lang, AGAIN["en"]))
        return None
    return text
