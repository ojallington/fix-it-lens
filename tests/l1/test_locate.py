"""Locate → segment, the 0.6 fallback to Spark's box, gas labels from the cue and the card only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import calls
import rules
from app import locate
from app.models import Models
from app.recorder import Recorder
from contracts import VERSION
from contracts.models.card import Card
from contracts.models.safety import Ok
from contracts.models.session import SessionMeta
from tests.l1.helpers import FakeClient, frame
from tests.l4.conftest import boiler_data

MASK = "M 100 100 L 300 100 L 300 300 L 100 300 Z"


def models(client: Any) -> Models:
    meta = SessionMeta(session_id="x", started_at=datetime.now(UTC), mode="api", lang="en",
                       contracts_version=VERSION)
    return Models(client, Recorder(None, meta), lambda: 0.0, household=None, session_id="x")


def sam(conf: float) -> calls.SamResult:
    return calls.SamResult(1000, 1000, [calls.SamObject("o", (100, 100, 300, 300), conf, MASK, 0)], "", 5.0, 0.0,
                           "0" * 64)


async def test_mask_under_06_falls_back_to_the_box_drawn_larger() -> None:
    out = await locate.segment(models(FakeClient(image=sam(0.5))), frame(1, 0), box=(100, 100, 300, 300))
    assert out.source == "box" and out.region is not None and out.region.box[0] < 100 and out.region.box[2] > 300
    ok = await locate.segment(models(FakeClient(image=sam(0.9))), frame(1, 0), box=(100, 100, 300, 300))
    assert ok.source == "sam" and ok.region is not None and ok.region.polys


def test_gas_label_from_the_cue_and_the_card_only() -> None:
    card = Card.model_validate(boiler_data())
    tap = next(p for p in card.parts if p.id == "gas_tap")
    assert locate.needs_spark(tap)  # never SAM by text
    assert locate.outline_label(card, tap, "en", cue_seen=False) == "possible gas: don't touch"
    assert locate.outline_label(card, tap, "en", cue_seen=True) == "gas: don't touch"
    for lang in ("en", "nl"):
        for cue in (True, False):  # the labels themselves pass G0, so the warning is never dropped
            label = locate.outline_label(card, tap, lang, cue_seen=cue)
            assert isinstance(rules.check_sentence(label, lang, card, Ok(state="OK"), country="nl"), rules.Pass), label
