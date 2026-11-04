"""On a step near a gas part the session outlines it and asks the user to confirm the outline before the
step. The ask never names the part (G0 holds any sentence that does); yes or confirm_gas_tap goes on, no outlines
again."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.session import GAS_AGAIN, GAS_CONFIRM, GAS_DETAIL, Session
from contracts.models.card import Card
from tests.l1.test_session import tap
from tests.l4.conftest import boiler_data


def boiler_session(tmp_path: Path, lang: str) -> tuple[Session, list[str]]:
    s = Session(session_id="gas1", client=object(), card=Card.model_validate(boiler_data()),  # type: ignore[arg-type]
                job_id="top_up", country="nl", lang=lang, record_root=tmp_path)
    s.guide = None
    s.out = []  # type: ignore[attr-defined]
    outlined: list[str] = []

    async def sink(m: dict[str, Any]) -> None:
        s.out.append(m)  # type: ignore[attr-defined]

    async def outline_part(part_id: str, *a: Any, **kw: Any) -> None:
        outlined.append(part_id)

    s.sinks.append(sink)
    s.outline_part = outline_part  # type: ignore[method-assign]
    return s, outlined


def said(s: Session) -> str:
    return " ".join(m["text"] for m in s.out if m["type"] == "sentence")  # type: ignore[attr-defined]


@pytest.mark.parametrize("lang", ["en", "nl"])
@pytest.mark.parametrize("action", ["confirm_gas_tap", "yes"])
async def test_confirm_passes_g0_and_goes_on(tmp_path: Path, lang: str, action: str) -> None:
    s, outlined = boiler_session(tmp_path, lang)
    s.start()
    await s.start_job()
    assert s.phase == "gas_confirm" and outlined == ["gas_tap"]
    assert GAS_CONFIRM[lang] in said(s)
    await s.handle(tap(action))
    await s.idle()
    assert s.gas_confirmed and s.phase != "gas_confirm"
    out = json.dumps(s.out)  # type: ignore[attr-defined]
    assert not any(m["type"] == "held" for m in s.out)  # type: ignore[attr-defined]
    assert "gas tap" not in out and "gaskraan" not in out
    await s.close()


async def test_no_outlines_again(tmp_path: Path) -> None:
    s, outlined = boiler_session(tmp_path, "en")
    s.start()
    await s.start_job()
    await s.handle(tap("no"))
    await s.idle()
    assert outlined == ["gas_tap", "gas_tap"] and s.phase == "gas_confirm" and not s.gas_confirmed
    assert said(s).count(GAS_CONFIRM["en"]) == 2 and GAS_AGAIN["en"] in said(s)
    await s.close()


async def test_done_does_not_skip_the_confirm(tmp_path: Path) -> None:
    s, _ = boiler_session(tmp_path, "en")
    s.start()
    await s.start_job()
    await s.handle(tap("repeat"))
    await s.idle()
    await s.handle(tap("back"))
    await s.idle()
    assert s.phase == "gas_confirm" and not s.gas_confirmed
    await s.close()


async def test_gas_confirm_raises_a_hold_the_phone_keys_on(tmp_path: Path) -> None:
    s, _ = boiler_session(tmp_path, "en")
    s.start()
    await s.start_job()
    safety = [m for m in s.out if m["type"] == "safety"]  # type: ignore[attr-defined]
    assert safety and safety[-1]["state"] == {"state": "HOLD", "precondition": "user_confirm", "rule_id": "nl.gas.tap",
                                              "detail": GAS_DETAIL}
    await s.handle(tap("stop"))
    await s.idle()
    await s.handle(tap("yes"))  # resumes the user's stop; the confirm is still owed
    await s.idle()
    assert not s.gas_confirmed and s._gas_held()
    await s.handle(tap("confirm_gas_tap"))
    await s.idle()
    assert s.gas_confirmed and not s._gas_held()
    await s.close()
