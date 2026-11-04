"""Free-credit pools and routing for public sessions: the admission check, the floors and the judges' reserve, Meta's
public sub-budget, the per-session pin, failover to the next pool and never to Meta's Spark or local Glimmer. No key
and no network: partners are replay clients or stand-ins, keys are files in tmp_path."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import calls
from calls.breaker import Breaker
from calls.config import Config, load_config
from calls.errors import BackendUnavailable, CallFailed, PoolsExhausted
from calls.ledger import Ledger
from calls.pools import Credits, Pool, Pools, load_credits
from calls.routing import Router
from calls.testing import responses_events, write_respond_fixture

CREDITS = """
session_usd = 1.0
session_meta_usd = 0.1
judges_reserve_usd = 10.0

[pool.baseten]
credit = 100.0
floor = 5.0

[pool.fireworks]
credit = 25.0
floor = 2.0
"""


def keyed(tmp_path: Path, *names: str, routing: bool = True) -> Config:
    """The repo config with routing on and a 0600 key file for each named partner."""
    base = load_config()
    partners = dict(base.partners)
    for name, p in base.partners.items():
        key = tmp_path / f"{name}.key"
        if name in names:
            key.write_text("k\n")
            key.chmod(0o600)
        partners[name] = replace(p, key_path=key)
    return replace(base, partners=partners, routing=routing)


def private(tmp_path: Path, credits: str = CREDITS, rows: list[dict[str, Any]] = ()) -> Path:  # type: ignore[assignment]
    d = tmp_path / "private"
    d.mkdir(exist_ok=True)
    (d / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl7 = 2.0\n")
    (d / "credits.toml").write_text(credits)
    (d / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def pools(tmp_path: Path, *keys: str, rows: list[dict[str, Any]] = (), credits: str = CREDITS) -> Pools:  # type: ignore[assignment]
    d = private(tmp_path, credits, rows)
    ledger = Ledger(d / "ledger.jsonl", d / "budgets.toml")
    return Pools(keyed(tmp_path, *keys), ledger, load_credits(d / "credits.toml"))


# ---- admission --------------------------------------------------------------------------------------------------


def test_with_routing_off_every_public_session_is_admitted() -> None:
    assert load_config().routing is False
    assert calls.pool_open("door") == (True, None)
    assert calls.pool_open("judges") == (True, None)


def test_pool_open_is_only_for_public_sessions() -> None:
    with pytest.raises(ValueError):
        calls.pool_open(cast(Any, None))


def test_no_key_means_no_pool(tmp_path: Path) -> None:
    p = pools(tmp_path)
    assert p.choose("judges") is None and p.open("door") == (False, None)


def test_the_first_pool_in_order_with_room(tmp_path: Path) -> None:
    p = pools(tmp_path, "baseten", "fireworks")
    assert p.choose("door") == "baseten" and p.open("door") == (True, None)


def test_a_pool_at_its_floor_passes_to_the_next(tmp_path: Path) -> None:
    rows = [{"provider": "baseten", "usd": 94.5}]  # 5.5 left, floor 5: no room for a 1.0 session
    p = pools(tmp_path, "baseten", "fireworks", rows=rows)
    assert p.remaining()["baseten"] == pytest.approx(5.5)
    assert p.choose("judges") == "fireworks"


def test_the_door_closes_first_so_the_judges_keep_a_reserve(tmp_path: Path) -> None:
    rows = [{"provider": "baseten", "usd": 90.0}]  # 10 left: above the floor, not above floor + reserve
    p = pools(tmp_path, "baseten", rows=rows)
    assert p.choose("judges") == "baseten"
    assert p.choose("door") is None and p.open("door") == (False, None)


def test_spend_the_ledger_never_saw(tmp_path: Path) -> None:
    p = pools(tmp_path, "baseten", credits=CREDITS.replace("floor = 5.0", "floor = 5.0\nspent_outside = 95.0"))
    assert p.choose("judges") is None


def test_lines_without_a_provider_are_meta(tmp_path: Path) -> None:
    p = pools(tmp_path, "baseten", rows=[{"usd": 99.0}, {"provider": "local", "usd": 0.0}])
    assert p.remaining()["baseten"] == 100.0


def test_meta_public_sub_budget_defaults_to_20_and_closes_the_door(tmp_path: Path) -> None:
    assert load_credits(None).meta_public_usd == 20.0
    rows = [{"provider": "meta", "origin": "door", "usd": 19.95}, {"provider": "meta", "usd": 50.0}]
    p = pools(tmp_path, "baseten", rows=rows)
    assert p.choose("door") == "baseten" and not p.meta_room()
    assert p.open("door") == (False, None) and p.open("judges") == (False, None)


def test_modal_stays_off_even_with_a_key(tmp_path: Path) -> None:
    p = pools(tmp_path, "modal", credits=CREDITS + "\n[pool.modal]\ncredit = 100.0\n")
    assert not p.usable("modal") and p.choose("judges") is None


def test_no_credits_file_closes_every_pool(tmp_path: Path) -> None:
    assert load_credits(tmp_path / "missing.toml") == Credits()
    p = Pools(keyed(tmp_path, "baseten"), None, Credits())
    assert p.choose("judges") is None


def test_an_unknown_pool_is_refused(tmp_path: Path) -> None:
    (tmp_path / "c.toml").write_text("[pool.groq]\ncredit = 5.0\n")
    with pytest.raises(ValueError, match="groq"):
        load_credits(tmp_path / "c.toml")


# ---- routing ----------------------------------------------------------------------------------------------------


class Stand:
    """A stand-in partner client: fails `fail` times with `error`, then answers."""

    def __init__(self, name: str, fail: int = 0, error: Exception | None = None) -> None:
        self.name, self.fail, self.error = name, fail, error
        self.calls: list[dict[str, Any]] = []

    async def respond(self, role: str, input: Any, **kw: Any) -> Any:
        self.calls.append({"role": role, **kw})
        if self.fail:
            self.fail -= 1
            raise self.error or CallFailed(f"{self.name} down")
        return f"{self.name}:{role}"


def router(tmp_path: Path, stands: dict[str, Stand], *keys: str) -> Router:
    return Router(pools(tmp_path, *keys), lambda n: cast(Any, stands[n]))


async def test_a_session_keeps_its_pool(tmp_path: Path) -> None:
    s = {"baseten": Stand("baseten"), "fireworks": Stand("fireworks")}
    r = router(tmp_path, s, "baseten", "fireworks")
    assert await r.respond("guide", "hi", origin="door", session_id="s1", lane="l7") == "baseten:guide"
    r.pools.ledger.append({"provider": "baseten", "usd": 95.0})  # type: ignore[union-attr]
    assert await r.respond("judge", "is it in?", origin="door", session_id="s1", lane="l7") == "baseten:judge"
    assert await r.respond("guide", "hi", origin="judges", session_id="s2", lane="l7") == "fireworks:guide"


async def test_two_outages_move_the_session_to_the_next_pool(tmp_path: Path) -> None:
    s = {"baseten": Stand("baseten", fail=5), "fireworks": Stand("fireworks")}
    r = router(tmp_path, s, "baseten", "fireworks")
    with pytest.raises(CallFailed):
        await r.respond("judge", "x", origin="door", session_id="s1", lane="l7")
    assert await r.respond("judge", "x", origin="door", session_id="s1", lane="l7") == "fireworks:judge"
    assert r.pins["s1"] == "fireworks" and r.moves[0][:2] == ("s1", "baseten")


async def test_a_partner_without_its_key_is_skipped_at_once(tmp_path: Path) -> None:
    s = {"baseten": Stand("baseten", fail=1, error=BackendUnavailable("baseten: waits on key")),
         "fireworks": Stand("fireworks")}
    r = router(tmp_path, s, "baseten", "fireworks")
    assert await r.respond("judge", "x", origin="judges", session_id="s1", lane="l7") == "fireworks:judge"


async def test_no_pool_left_is_pools_exhausted_never_meta(tmp_path: Path) -> None:
    s = {"baseten": Stand("baseten", fail=9)}
    r = router(tmp_path, s, "baseten")
    with pytest.raises(CallFailed):
        await r.respond("judge", "x", origin="door", session_id="s1", lane="l7")
    with pytest.raises(PoolsExhausted):
        await r.respond("judge", "x", origin="door", session_id="s1", lane="l7")


async def test_no_key_at_all_is_pools_exhausted(tmp_path: Path) -> None:
    r = router(tmp_path, {})
    with pytest.raises(PoolsExhausted):
        await r.respond("judge", "x", origin="door", session_id="s1", lane="l7")


def meta_and_partner(tmp_path: Path) -> tuple[calls.Client, calls.Client]:
    cfg = keyed(tmp_path, "baseten")
    d = private(tmp_path)
    meta = calls.make_client("replay", config=replace(cfg, routing=False), fixtures_root=tmp_path / "f", private=d)
    partner = calls.make_client("replay", config=cfg, fixtures_root=tmp_path / "f", private=d, partner="baseten")
    meta.router = Router(Pools(cfg, Ledger(d / "ledger.jsonl", d / "budgets.toml"), load_credits(d / "credits.toml")),
                         lambda n: partner)
    return meta, partner


async def test_only_public_partner_role_turns_are_routed(tmp_path: Path) -> None:
    meta, partner = meta_and_partner(tmp_path)
    write_respond_fixture(tmp_path / "f", meta.build_respond_body("guide", "hi"), responses_events(text="meta"))
    write_respond_fixture(tmp_path / "f", partner.build_respond_body("guide", "hi"),
                          responses_events(text="spark open", model="meta-models/muse-spark-open"))
    assert (await meta.respond("guide", "hi", lane="l7")).text == "meta"  # no origin: an invite home, on Meta
    assert (await meta.respond("guide", "hi", lane="l7", origin="door", session_id="s")).text == "spark open"
    write_respond_fixture(tmp_path / "f", meta.build_respond_body("locate", "where?"), responses_events(text="box"))
    assert (await meta.respond("locate", "where?", lane="l7", origin="door")).text == "box"  # not a partner role


def test_make_client_routes_only_when_routing_is_on(tmp_path: Path) -> None:
    assert calls.make_client("replay", private=None).router is None
    on = calls.make_client("replay", config=keyed(tmp_path, "baseten"), private=private(tmp_path))
    assert isinstance(on.router, Router)
    assert calls.make_client("replay", config=keyed(tmp_path), private=None, partner="baseten").router is None


# ---- no local overflow ------------------------------------------------------------------------------------------


class Down:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.n = 0

    async def respond(self, role: str, input: Any, **kw: Any) -> Any:
        self.n += 1
        raise CallFailed("down")


async def test_a_public_session_never_falls_back_to_local() -> None:
    cfg = load_config()
    api, local = Down(cfg), Down(cfg.local())
    b = Breaker(cast(Any, api), cast(Any, local), private=None)
    for _ in range(3):
        with pytest.raises(CallFailed):
            await b.respond("judge", "x", lane="l7", origin="judges")
    assert b.mode == "api" and local.n == 0


async def test_an_operators_home_still_falls_back_to_local() -> None:
    cfg = replace(load_config(), local_homes=("h_mine",))
    api, local = Down(cfg), Down(cfg.local())
    b = Breaker(cast(Any, api), cast(Any, local), private=None)
    with pytest.raises(CallFailed):
        await b.respond("judge", "x", lane="l7", household="h_mine")
    with pytest.raises(CallFailed):
        await b.respond("judge", "x", lane="l7", household="h_mine")
    assert b.mode == "local" and local.n == 1


# ---- the origin on every call -----------------------------------------------------------------------------------


def test_every_call_models_makes_takes_an_origin() -> None:
    """app/models.py sends origin= (door, judges) on these four when the household has one."""
    import inspect

    for name in ("respond", "sam_image", "sam_video", "transcribe"):
        p = inspect.signature(getattr(calls.Client, name)).parameters["origin"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is None


async def test_with_routing_off_an_origin_changes_nothing(tmp_path: Any) -> None:
    client = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    write_respond_fixture(tmp_path, client.build_respond_body("guide", "hi"), responses_events(text="hello"))
    assert (await client.respond("guide", "hi", lane="l7", origin="door")).text == "hello"


def test_the_pool_dataclass_defaults() -> None:
    assert Pool("baseten", 100.0) == Pool("baseten", 100.0, 0.0, 0.0)


async def test_a_public_sessions_meta_spend_counts_against_the_sub_budget(tmp_path: Path) -> None:
    """End to end: a door session's call writes origin on its ledger line, and Meta's public sub-budget counts it."""

    class Meta:
        name = "meta"

        async def stream(self, body: dict[str, Any], *, sha: str, kind: str) -> Any:
            for ev in responses_events(text="ok", input_tokens=4_000_000, output_tokens=4000):
                yield ev

    d = private(tmp_path, "meta_public_usd = 5.0\n" + CREDITS)  # top level: before the [pool.*] tables
    ledger = Ledger(d / "ledger.jsonl", d / "budgets.toml")
    (d / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl7 = 50.0\n")
    cfg = keyed(tmp_path, "baseten", routing=False)
    c = calls.Client("live", cfg, cast(Any, Meta()), ledger=ledger, fixtures_root=tmp_path, private=d)
    p = Pools(cfg, ledger, load_credits(d / "credits.toml"))
    await c.respond("locate", "where?", lane="l7", origin="door", session_id="s1")
    await c.respond("locate", "where?", lane="l7", session_id="s2")  # an invite home: not public
    [door, invite] = [json.loads(x) for x in (d / "ledger.jsonl").read_text().splitlines()]
    assert door["origin"] == "door" and door["provider"] == "meta" and invite["origin"] is None
    assert ledger.spend().meta_public == pytest.approx(door["usd"]) and door["usd"] > 5.0
    assert not p.meta_room() and p.open("judges") == (False, None)


# ---- the operator's line on /preflight --------------------------------------------------------------------------
# deploy/preflight.html picks its plain sentence from the detail's opening words ('Off', 'On', 'On, but full'),
# so these tests pin them; an error starts with none of them.


async def test_the_credits_line_with_routing_off_is_not_a_fault(tmp_path: Path) -> None:
    d = private(tmp_path, rows=[{"provider": "fireworks", "usd": 3.5}])
    line = await calls.preflight_credits(keyed(tmp_path, routing=False), d)
    assert line["ok"] is True
    assert line["detail"] == ("Off: every session runs on Meta. Baseten: waits on key, $100.00 left ($5.00 kept "
                              "back) · Fireworks: waits on key, $21.50 left ($2.00 kept back) · Modal: off · Meta "
                              "for public sessions: $20.00 of $20.00 left")


async def test_the_credits_line_with_routing_on(tmp_path: Path) -> None:
    line = await calls.preflight_credits(keyed(tmp_path, "baseten"), private(tmp_path))
    assert line["ok"] is True and line["detail"].startswith("On: a new public session fits. Baseten: ready, $100.00")


async def test_the_credits_line_when_every_pool_is_full(tmp_path: Path) -> None:
    line = await calls.preflight_credits(keyed(tmp_path), private(tmp_path))
    assert line["ok"] is False and line["detail"].startswith("On, but full: new public sessions are turned away.")


async def test_the_credits_line_never_raises(tmp_path: Path) -> None:
    d = private(tmp_path, credits="[pool.groq]\ncredit = 1.0\n")
    assert await calls.preflight_credits(keyed(tmp_path), d) == {
        "ok": False, "detail": "Could not read the credit figures (ValueError)"}
