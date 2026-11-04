"""The circuit breaker: two recorded API failures switch the session to local mode, once, with the
badge hook; the tripping call is answered locally. Only the operator's own homes, or every home on a self-hosted
instance, ever switch: never an invited home, a call with no household, or a public session. Replay only."""

from __future__ import annotations

from pathlib import Path

import pytest

import calls
from calls.testing import responses_events, write_respond_fixture

ERROR = [(120.0, {"type": "error", "code": "server_error", "message": "upstream unavailable", "sequence_number": 0})]


def pair(tmp_path: Path) -> tuple[calls.Client, calls.Client]:
    api = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    local = calls.make_client("replay", fixtures_root=tmp_path, private=None, local=True)
    return api, local


MINE = {"lane": "l7", "household": "h_mine"}  # the operator's own home in these tests


def private(tmp_path: Path, text: str = 'homes = ["h_mine"]\n') -> Path:
    d = tmp_path / "private"
    d.mkdir(exist_ok=True)
    (d / "local_fallback.toml").write_text(text)
    return d


def record(client: calls.Client, tmp_path: Path, prompt: str, events: list) -> None:  # type: ignore[type-arg]
    write_respond_fixture(tmp_path, client.build_respond_body("guide", prompt), events)


async def test_two_failures_trip_once_and_the_call_is_answered_locally(tmp_path: Path) -> None:
    api, local = pair(tmp_path)
    for p in ("one", "two", "three"):
        record(api, tmp_path, p, ERROR)
        record(local, tmp_path, p, responses_events(text=f"local {p}", model="muse-glimmer:30b"))
    badges: list[str] = []
    b = calls.Breaker(api, local, on_switch=badges.append, private=private(tmp_path))
    with pytest.raises(calls.CallFailed):
        await b.respond("guide", "one", **MINE)
    assert b.mode == "api" and badges == []
    r = await b.respond("guide", "two", **MINE)
    assert r.text == "local two" and r.model == "muse-glimmer:30b"
    assert b.mode == "local" and len(badges) == 1 and "CallFailed" in badges[0]
    assert (await b.respond("guide", "three", **MINE)).text == "local three"
    assert len(badges) == 1  # the badge fires once; the session stays local


async def test_a_success_resets_the_count(tmp_path: Path) -> None:
    api, local = pair(tmp_path)
    record(api, tmp_path, "a", ERROR)
    record(api, tmp_path, "b", responses_events(text="api ok"))
    record(api, tmp_path, "c", ERROR)
    b = calls.Breaker(api, local, private=private(tmp_path))
    with pytest.raises(calls.CallFailed):
        await b.respond("guide", "a", **MINE)
    assert (await b.respond("guide", "b", **MINE)).text == "api ok"
    with pytest.raises(calls.CallFailed):  # fail, ok, fail: never two in a row
        await b.respond("guide", "c", **MINE)
    assert b.mode == "api"


async def test_an_incomplete_reply_is_not_an_outage(tmp_path: Path) -> None:
    api, local = pair(tmp_path)
    for attempt in (1, 2):
        write_respond_fixture(tmp_path, api.build_respond_body("guide", "x", attempt=attempt),
                              responses_events(status="incomplete"))
    b = calls.Breaker(api, local, trip_after=1, private=private(tmp_path))
    with pytest.raises(calls.IncompleteResponse):
        await b.respond("guide", "x", **MINE)
    assert b.mode == "api"


async def test_async_badge_hook_and_no_local_speech(tmp_path: Path) -> None:
    api, local = pair(tmp_path)
    record(api, tmp_path, "x", ERROR)
    record(local, tmp_path, "x", responses_events(text="ok", model="muse-glimmer:30b"))
    seen: list[str] = []

    async def badge(why: str) -> None:
        seen.append(why)

    b = calls.Breaker(api, local, on_switch=badge, trip_after=1, private=private(tmp_path))
    await b.respond("guide", "x", **MINE)
    assert seen and b.active is local


def test_the_second_client_must_be_local(tmp_path: Path) -> None:
    api, _ = pair(tmp_path)
    with pytest.raises(ValueError):
        calls.Breaker(api, api, private=None)


# ---- whose home may use the models on this machine ----------------------------------------------------------------


def down(tmp_path: Path, *prompts: str) -> tuple[calls.Client, calls.Client]:
    api, local = pair(tmp_path)
    for p in prompts:
        record(api, tmp_path, p, ERROR)
        record(local, tmp_path, p, responses_events(text="local", model="muse-glimmer:30b"))
    return api, local


@pytest.mark.parametrize("kw", [
    {"household": "h_invited"},  # someone the operator invited: their home is not the operator's
    {},  # no household at all
    {"household": "h_mine", "origin": "door"},  # the open door, even with an operator's id on it
    {"household": None, "origin": "judges"},
], ids=["invited", "no-household", "door", "judges"])
async def test_only_the_operators_own_homes_fall_back(tmp_path: Path, kw: dict[str, str]) -> None:
    api, local = down(tmp_path, "a", "b", "c")
    badges: list[str] = []
    b = calls.Breaker(api, local, on_switch=badges.append, private=private(tmp_path))
    for p in ("a", "b", "c"):
        with pytest.raises(calls.CallFailed):
            await b.respond("guide", p, lane="l7", **kw)
    assert b.mode == "api" and badges == []


async def test_with_no_private_list_no_home_falls_back(tmp_path: Path) -> None:
    api, local = down(tmp_path, "a", "b")
    b = calls.Breaker(api, local, private=None)  # the committed config names no homes and is not self-hosted
    assert (b.self_hosted, b.homes) == (False, frozenset())
    for p in ("a", "b"):
        with pytest.raises(calls.CallFailed):
            await b.respond("guide", p, **MINE)
    assert b.mode == "api"


async def test_a_self_hosted_instance_falls_back_for_its_own_homes_but_never_a_public_session(tmp_path: Path) -> None:
    api, local = down(tmp_path, "a", "b", "c", "d")
    b = calls.Breaker(api, local, private=private(tmp_path, "self_hosted = true\n"))
    for p in ("a", "b"):
        with pytest.raises(calls.CallFailed):
            await b.respond("guide", p, lane="l7", origin="door")
    assert b.mode == "api"
    with pytest.raises(calls.CallFailed):
        await b.respond("guide", "c", lane="l7", household="h_anyone")
    assert (await b.respond("guide", "d", lane="l7", household="h_anyone")).text == "local"
    assert b.mode == "local"


async def test_after_a_switch_a_public_call_still_goes_to_the_api(tmp_path: Path) -> None:
    api, local = down(tmp_path, "a", "b", "c")
    b = calls.Breaker(api, local, private=private(tmp_path))
    with pytest.raises(calls.CallFailed):
        await b.respond("guide", "a", **MINE)
    await b.respond("guide", "b", **MINE)
    assert b.mode == "local"
    with pytest.raises(calls.CallFailed):
        await b.respond("guide", "c", lane="l7", origin="judges")


def test_the_committed_config_names_no_home_and_is_not_self_hosted() -> None:
    cfg = calls.load_config()
    assert cfg.local_homes == () and cfg.self_hosted is False


def test_local_overflow_is_refused_in_config(tmp_path: Path) -> None:
    from calls.config import DEFAULT_CONFIG

    f = tmp_path / "models.toml"
    f.write_text(DEFAULT_CONFIG.read_text().replace("[partner]\n", "[partner]\nlocal_overflow = true\n", 1))
    with pytest.raises(calls.ConfigError, match="local_overflow"):
        calls.load_config(f)
