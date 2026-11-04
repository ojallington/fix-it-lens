"""/preflight: every line green or red; a check that cannot run or breaks is red, never a 500; no network here."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.feeds import preflight
from app.feeds.router import Deps, make_router
from calls.ledger import Ledger

ROOT = Path(__file__).resolve().parents[2]


def run(c: preflight.Check) -> preflight.Line:
    return asyncio.run(c())


def ledger(tmp_path: Path, spent: dict[str, float]) -> Ledger:
    (tmp_path / "budgets.toml").write_text('total_cap = 100\n[lanes]\nl1 = 40\npilot = 10\nreserve = 20\n')
    (tmp_path / "ledger.jsonl").write_text("".join(json.dumps({"lane": k, "usd": v}) + "\n" for k, v in spent.items()))
    return Ledger(tmp_path / "ledger.jsonl", tmp_path / "budgets.toml")


def test_ledger_headroom(tmp_path: Path) -> None:
    ok = run(preflight.ledger_check(ledger(tmp_path, {"l1": 3.0})))
    assert ok.ok and "$97.00 left of $100" in ok.detail
    low = run(preflight.ledger_check(ledger(tmp_path, {"pilot": 9.5})))
    assert not low.ok and "pilot $0.50" in low.detail
    assert not run(preflight.ledger_check(None)).ok


def test_calls_mode_line(tmp_path: Path) -> None:
    led = ledger(tmp_path, {"l1": 6.5})
    assert run(preflight.calls_check(led, "record")) == preflight.Line("calls", True,
                                                                        "record (live, ledger $6.50 of $100)")
    assert run(preflight.calls_check(None, "replay")).ok
    assert not run(preflight.calls_check(None, "live")).ok
    assert not run(preflight.calls_check(led, "fast")).ok


def test_manuals_red_for_a_card_without_its_pages() -> None:
    have = run(preflight.manuals_check(ROOT / "cards", lambda brand, model: [object()]))
    assert have.ok and "manuals in place" in have.detail
    none = run(preflight.manuals_check(ROOT / "cards", lambda brand, model: []))
    assert not none.ok and "missing: bosch SMV4HAX40N" in none.detail


def recording(root: Path, sid: str, events: list[dict[str, Any]]) -> None:
    d = root / sid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"session_id": sid, "started_at": "2026-10-27T12:00:00Z",
                                             "mode": "api", "lang": "en", "contracts_version": "1.3.0"}))
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_last_replay(tmp_path: Path) -> None:
    assert not run(preflight.replay_check(tmp_path / "nope")).ok
    tmp_path.joinpath("s").mkdir()
    assert run(preflight.replay_check(tmp_path / "s")).detail == "no recording"
    recording(tmp_path / "s", "r1", [{"dir": "s2c", "msg": {}}, {"dir": "c2s", "msg": {}}])
    line = run(preflight.replay_check(tmp_path / "s"))
    assert line.ok and line.detail == "r1: 2 events"
    (tmp_path / "s/r1/events.jsonl").write_text("{not json\n")
    assert not run(preflight.replay_check(tmp_path / "s")).ok


def test_probes_and_ha() -> None:
    async def green() -> dict[str, Any]:
        return {"ok": True, "detail": "3 pinned models listed"}

    assert run(preflight.probe_check("models", green)) == preflight.Line("models", True, "3 pinned models listed")
    assert not run(preflight.probe_check("local", None)).ok

    async def down() -> bool:
        return False

    assert not run(preflight.ha_check(down)).ok
    assert run(preflight.ha_check(None)).ok  # a home without HA is fine


def test_cert_unreachable_is_red() -> None:
    line = run(preflight.cert_check("127.0.0.1", 1))
    assert not line.ok and line.check == "certificate 127.0.0.1:1"


def test_a_broken_or_slow_check_is_a_red_line(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> preflight.Line:
        raise RuntimeError("kaput")

    async def slow() -> preflight.Line:
        await asyncio.sleep(10)
        raise AssertionError

    monkeypatch.setattr(preflight, "CHECK_TIMEOUT_S", 0.01)
    lines = asyncio.run(preflight.run_checks([("boom", boom), ("slow", slow)]))
    assert lines == [preflight.Line("boom", False, "check failed: RuntimeError: kaput"),
                     preflight.Line("slow", False, "timed out")]


@pytest.fixture
def api(conn: sqlite3.Connection, secret: bytes, loader: Any) -> Iterator[tuple[TestClient, list[Any]]]:
    seen: list[Any] = []

    async def green() -> preflight.Line:
        return preflight.Line("models", True, "ok")

    def checks(hosts: list[tuple[str, int]]) -> list[tuple[str, preflight.Check]]:
        seen.append(hosts)
        return [("models", green)]

    app = FastAPI()
    app.include_router(make_router(Deps(conn, secret, loader, preflight=checks)), prefix="/api/v1")
    with TestClient(app, base_url="https://app.test:8443") as c:
        yield c, seen


def test_route_checks_the_certificate_it_was_opened_on(api: tuple[TestClient, list[Any]]) -> None:
    client, seen = api
    r = client.get("/api/v1/preflight")
    assert r.status_code == 200 and r.json() == [{"check": "models", "ok": True, "detail": "ok"}]
    assert seen == [[("app.test", 8443)]]
    r = client.get("http://app.test/api/v1/preflight")
    assert r.json()[-1]["check"] == "certificate" and not r.json()[-1]["ok"]


def test_warm_up_one_at_a_time(conn: sqlite3.Connection, secret: bytes, loader: Any) -> None:
    gate = asyncio.Event()
    calls_made: list[int] = []

    async def warm() -> dict[str, Any]:
        calls_made.append(1)
        await gate.wait()
        return {"ok": True, "detail": "muse-glimmer loaded"}

    app = FastAPI()
    app.include_router(make_router(Deps(conn, secret, loader, warm_up=warm)), prefix="/api/v1")

    import httpx

    async def go() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t") as c:
            first = asyncio.create_task(c.post("/api/v1/preflight/warm-up"))
            while not calls_made:
                await asyncio.sleep(0)
            second = await c.post("/api/v1/preflight/warm-up")
            gate.set()
            return await first, second

    first, second = asyncio.run(go())
    assert first.json() == {"check": "warm up", "ok": True, "detail": "muse-glimmer loaded"}
    assert second.status_code == 409 and second.json()["error"] == "warming_up" and calls_made == [1]


def test_no_warm_up_wired(api: tuple[TestClient, list[Any]]) -> None:
    client, _ = api
    r = client.post("/api/v1/preflight/warm-up")
    assert r.status_code == 200 and not r.json()["ok"]


def test_warm_up_cooldown(conn: sqlite3.Connection, secret: bytes, loader: Any) -> None:
    from datetime import UTC, datetime, timedelta

    now = [datetime(2026, 10, 27, 12, 0, tzinfo=UTC)]
    results = [{"ok": False, "detail": "ollama down"}, {"ok": True, "detail": "loaded"}]

    async def warm() -> dict[str, Any]:
        return results.pop(0)

    app = FastAPI()
    app.include_router(make_router(Deps(conn, secret, loader, clock=lambda: now[0], warm_up=warm)), prefix="/api/v1")
    c = TestClient(app)
    assert not c.post("/api/v1/preflight/warm-up").json()["ok"]
    now[0] += timedelta(seconds=20)
    r = c.post("/api/v1/preflight/warm-up")  # 60 s after any finished warm-up, failed or not
    assert r.status_code == 409 and r.json() == {"error": "cooldown", "detail": "warmed up just now; try again in 41 s"}
    assert r.headers["retry-after"] == "41"
    now[0] += timedelta(seconds=41)
    assert c.post("/api/v1/preflight/warm-up").json() == {"check": "warm up", "ok": True, "detail": "loaded"}


def test_replay_mode_needs_no_key_or_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import calls

    async def no(*a: Any, **k: Any) -> Any:
        raise AssertionError("replay probed the models")
    monkeypatch.setattr(calls, "preflight_models", no)
    lines = {n: run(c) for n, c in preflight.default_checks([], ledger=None, private=None, cards_dir=tmp_path,
                                                             mode="replay")}
    assert lines["models"].ok and "no key needed" in lines["models"].detail
    assert lines["ledger"] == preflight.Line("ledger", True, "replay: nothing spent")
    async def probed() -> dict[str, Any]:
        return {"ok": False, "detail": "probed"}
    monkeypatch.setattr(calls, "preflight_models", probed)
    live = dict(preflight.default_checks([], ledger=None, private=None, cards_dir=tmp_path, mode="record"))
    assert not run(live["ledger"]).ok and run(live["models"]).detail == "probed"


def test_no_ha_token_means_not_set_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def down() -> bool:
        return False
    monkeypatch.setenv("FIXIT_HA_TOKEN_FILE", str(tmp_path / "none"))
    ha = dict(preflight.default_checks([], ledger=None, private=None, cards_dir=tmp_path, ha_available=down,
                                       mode="replay"))["home assistant"]
    assert run(ha) == preflight.Line("home assistant", True, "not set up (optional)")
    (tmp_path / "none").write_text("t")
    ha = dict(preflight.default_checks([], ledger=None, private=None, cards_dir=tmp_path, ha_available=down,
                                       mode="replay"))["home assistant"]
    assert not run(ha).ok


def test_the_check_page_reads_as_a_product_page() -> None:
    """Polish #24: a titled system check with ✓/✗ text, no polling of a token-only route, no internal names."""
    html = (ROOT / "deploy/preflight.html").read_text()
    assert "<title>Fix-It Lens — system check</title>" in html
    assert "✓" in html and "✗" in html and "Not set up here" in html
    assert "vapid-public-key" not in html  # the latency ping uses a public file, not a 401


def page_eval(js: str) -> Any:
    """Run the check page's script in node with a stub browser, then `js`; returns what `js` prints as JSON."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node")
    html = (ROOT / "deploy/preflight.html").read_text()
    script = html.split("<script>", 1)[1].split("</script>", 1)[0].rstrip()
    assert script.endswith("go();")
    stub = """
const isSecureContext = true, performance = {now: () => 0};
const fetch = async () => ({ok: true});
let REG = null, CONTROLLER = null, HAS_SW = true, SETTLES = true;
const navigator = {
  mediaDevices: {}, getBattery: async () => { throw new Error("no"); },
  get serviceWorker() {
    const reg = () => SETTLES ? Promise.resolve(REG) : new Promise(() => {});
    return HAS_SW ? {controller: CONTROLLER, getRegistration: reg} : undefined;
  },
};
"""
    code = stub + script[: -len("go();")] + "\n(async () => { console.log(JSON.stringify(await (async () => {" + js + \
        "})())); })();"
    out = subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


SERVER_8 = [{"check": c, "ok": True, "detail": d} for c, d in [
    ("models", "4/4 pinned models listed"), ("local", "installed, loads on first use"),
    ("calls", "record (live, ledger $8.67 of $100)"), ("ledger", "$91.33 left of $100"),
    ("manuals", "1 of 1 manuals in place"), ("last replay", "x: 37 events"), ("home assistant", "answers (read-only)"),
    ("certificate h:8443", "valid 50 more days (to 2026-12-16)")]]


def test_a_first_visit_is_not_a_fault() -> None:
    """Finding N3: before the app has installed its service worker the line is grey 'Not checked yet', so a server
    at 8/8 reads 'All set', not '1 thing needs attention'."""
    import json

    got = page_eval(f"""
      const mine = await phone(); const sw = mine.find(l => l.check === "service worker");
      const first = {{sw: [state(sw), offText(sw), plain(sw)], sum: summary([...{json.dumps(SERVER_8)}, ...mine])}};
      REG = {{active: {{}}}}; const later = (await phone()).find(l => l.check === "service worker");
      SETTLES = false; REG = null; const hung = (await phone()).find(l => l.check === "service worker");
      SETTLES = true; HAS_SW = false; const none = (await phone()).find(l => l.check === "service worker");
      return {{first, later: [later.ok, state(later)], hung: state(hung), none: [state(none), plain(none)]}};""")
    assert got["first"]["sw"] == ["off", "Not checked yet", "Open the app once, then check again."]
    assert got["first"]["sum"] == {"bad": 0, "text": "All set · 1 not set up here · 1 not checked yet"}  # battery
    assert got["later"] == [True, "ok"]  # registered by the app, even when it does not control this page
    assert got["hung"] == "off"  # a registration lookup that never settles does not stall the page
    assert got["none"] == ["bad", "This browser can't keep the app offline."]


def test_replay_mode_greys_what_it_does_not_use(tmp_path: Path) -> None:
    """Finding N3: in replay mode the local model (and the Meta models) read 'Not used in replay mode', grey."""
    import json

    lines = [run(c).json() for _, c in preflight.default_checks([], ledger=None, private=None, cards_dir=tmp_path,
                                                                 mode="replay")]
    local = next(line for line in lines if line["check"] == "local")
    assert local == {"check": "local", "ok": True, "detail": "replay: not used"}
    got = page_eval(f"""const ls = {json.dumps(lines)};
      return ls.filter(l => l.check === "local" || l.check === "models").map(l => [state(l), offText(l), plain(l)]);""")
    assert got == [["off", "Not used in replay mode", "Not needed in replay mode."],
                   ["off", "Not used in replay mode", "Recordings play without it."]]
    red = page_eval('return state({check: "local", ok: false, detail: "ConnectError"});')
    assert red == "bad"  # outside replay mode an unreachable local model still needs attention


def test_the_free_credits_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Free-credit routing for public sessions: grey while it is off or in replay mode, green while a session fits,
    red when the pools are full. The figures stay under Details; /preflight is tailnet-only."""
    import json

    import calls

    lines = [run(c).json() for _, c in preflight.default_checks([], ledger=None, private=None, cards_dir=tmp_path,
                                                                 mode="replay")]
    assert next(line for line in lines if line["check"] == "credits") == {
        "check": "credits", "ok": True, "detail": "replay: not used"}

    async def credits() -> dict[str, object]:
        return {"ok": True, "detail": "Off: every session runs on Meta. Meta for public sessions: $20.00 left"}

    monkeypatch.setattr(calls, "preflight_credits", credits)
    live = dict(preflight.default_checks([], ledger=None, private=None, cards_dir=tmp_path, mode="record"))
    off = run(live["credits"]).json()
    assert off["ok"] and off["detail"].startswith("Off:")
    full = {"check": "credits", "ok": False, "detail": "On, but full: new public sessions are turned away. x"}
    fits = {"check": "credits", "ok": True, "detail": "On: a new public session fits. x"}
    broken = {"check": "credits", "ok": False, "detail": "Could not read the credit figures (ValueError)"}
    got = page_eval(f"""return [{json.dumps(lines)}.find(l => l.check === "credits"), {json.dumps(off)},
      {json.dumps(fits)}, {json.dumps(full)}, {json.dumps(broken)}]
      .map(l => [label(l.check), state(l), offText(l), plain(l)]);""")
    assert got == [["Free credits", "off", "Not used in replay mode", "Recordings play without them."],
                   ["Free credits", "off", "Turned off", "Off: every public session runs on Meta."],
                   ["Free credits", "ok", "Not set up here", "On: a new public session fits."],
                   ["Free credits", "bad", "Not set up here", "Full: new public sessions are turned away."],
                   ["Free credits", "bad", "Not set up here", "Can't read the credit figures."]]  # not "full"
