"""config/models.toml, the README and the code tell the same truth about local mode: Glimmer boxes the part and
the second witness is Glimmer plus optical flow; the sam rows reach SAM 3.1 only with FIXIT_LOCAL_SAM=1. No network:
without that switch the local client has no SAM address to send to."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any, cast

import pytest

import calls
from app import power
from calls import load_config, make_client
from calls.backends import local as backend
from contracts.models.card import Card
from local import witness as w
from rules import Evidence, StepHold, StepOk, check_step
from tests.l1.helpers import frame
from tests.l1.test_locate import models
from tests.l4.conftest import acme_data

ROOT = Path(__file__).resolve().parents[2]


def local_client(private: Path) -> calls.Client:
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl7 = 2.0\n")
    return make_client("live", local=True, private=private)


def test_local_mode_runs_no_sam_unless_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIXIT_LOCAL_SAM", raising=False)
    cfg = load_config()
    assert not cfg.local_sam_on and cfg.local_sam_url  # the address is configured, and unused
    client = local_client(tmp_path)
    assert cast(backend.LocalBackend, client.backend).sam_url == ""
    assert asyncio.run(w.provider(client)) is w.track_witness
    with pytest.raises(calls.BackendUnavailable):
        asyncio.run(client.sam_image(frame(1, 0).jpeg, (100, 100, 300, 300), lane="l7"))


def test_the_local_outline_is_glimmers_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import locate

    monkeypatch.delenv("FIXIT_LOCAL_SAM", raising=False)
    out = asyncio.run(locate.segment(models(local_client(tmp_path)), frame(1, 0), box=(100, 100, 300, 300)))
    assert out.source == "box" and out.region is not None and out.region.source == "box"


class ZeroWatts:
    """Home Assistant reading the appliance at 0 W."""

    async def entities(self, alias: str) -> tuple[bool, list[dict[str, Any]]]:
        return True, []

    async def power_w(self, alias: str) -> float | None:
        return 0.0

    async def power_state(self, alias: str) -> Any:
        return "off"


def test_a_power_off_step_stays_held_in_local_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The plug-out check needs SAM. With none, the camera can't look, HA's 0 W does not stand in for it, and a tap
    of yes (the session re-runs the same check) cannot either: the rules hold the step."""
    monkeypatch.delenv("FIXIT_LOCAL_SAM", raising=False)
    ev = asyncio.run(power.power_evidence(models(local_client(tmp_path)), frame(1, 0), ZeroWatts(), "washer"))
    assert ev.camera == "cant_see" and ev.ha == "done" and ev.witness().result == "cant_see"
    card = Card.model_validate(acme_data())
    step = next(s for j in card.jobs for s in j.steps if s.id == "open_flap")
    seen = Evidence(plug_out_seen=ev.witness().result == "done", ha_power_w=ev.ha_w)
    assert isinstance(check_step(step, card, seen, country="nl"), StepHold)


async def test_a_plug_out_seen_before_the_switch_still_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exception: the camera saw the plug out in api mode, then the breaker switched the session to local. The
    session keeps what it saw, so a later power-off step passes the rules without SAM."""
    priv = tmp_path / "private"  # a self-hosted instance: the breaker may fall back for any home (local-operator-only)
    priv.mkdir()
    (priv / "local_fallback.toml").write_text("self_hosted = true\n")
    monkeypatch.setenv("FIXIT_PRIVATE", str(priv))
    from app.session import Session
    from calls.testing import responses_events, write_respond_fixture
    from tests.l1.test_session import acme
    from tests.l7.test_breaker import ERROR

    api = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    local = calls.make_client("replay", fixtures_root=tmp_path, private=None, local=True)
    for p in ("one", "two"):
        write_respond_fixture(tmp_path, api.build_respond_body("guide", p), ERROR)
        write_respond_fixture(tmp_path, local.build_respond_body("guide", p),
                              responses_events(text=f"local {p}", model="muse-glimmer:30b"))
    s = Session(session_id="t7", client=api, fallback=local, card=acme(), job_id="clean_debris_filter",
                country="nl", record_root=tmp_path / "sessions")
    s.guide = None
    s.power_off_seen = True  # what the api-mode check set on the unplug step
    for p in ("one", "two"):
        with contextlib.suppress(calls.CallFailed):  # the first call fails over to nothing yet; the second switches
            await s.client.respond("guide", p, lane="l7")
    assert s.mode == "local"
    step = next(st for j in s.card.jobs for st in j.steps if st.id == "open_flap")
    ev = s.evidence(step)
    assert ev.plug_out_seen
    assert isinstance(check_step(step, s.card, Evidence(plug_out_seen=ev.plug_out_seen, ha_power_w=0.0),
                                 country="nl"), StepOk)


def test_config_and_readme_say_so() -> None:
    toml = (ROOT / "config" / "models.toml").read_text()
    local = toml[toml.index("# ---- local mode"):]
    assert "no SAM beside Glimmer" in local and "FIXIT_LOCAL_SAM=1" in local
    assert "SAM 3.1 open weights through local/sam_server" not in local
    readme = (ROOT / "README.md").read_text()
    assert "there is no SAM beside" in readme and "SAM 3.1 server for eval (never beside Glimmer)" in readme
    assert ("A step that needs the power off\n  cannot pass in local mode, unless the camera already saw a plug out"
            " of a socket before the switch") in readme
