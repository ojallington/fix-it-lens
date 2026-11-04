"""calls/ acceptance (brief §7): replay only, synthetic fixtures, no key, no network."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import wave
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

import calls
from calls.backends.replay import ReplayBackend
from calls.client import Client
from calls.config import load_config
from calls.errors import BudgetExceeded, CallTimeout, ConfigError, IncompleteResponse, MissingFixture, ModelMismatch
from calls.fixtures import request_sha256, write_fixture
from calls.ledger import Ledger
from calls.testing import FakeObject, responses_events, sam_events, write_respond_fixture


def jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 130, 140)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def replay(tmp_path: Path) -> Client:
    return calls.make_client("replay", fixtures_root=tmp_path, private=tmp_path / "private")


async def test_respond_replays_a_fixture(tmp_path: Path) -> None:
    client = replay(tmp_path)
    body = client.build_respond_body("judge", "is the filter out?", effort="minimal")
    write_respond_fixture(tmp_path, body, responses_events(text='{"clauses": []}'))
    r = await client.respond("judge", "is the filter out?", effort="minimal", lane="l0")
    assert r.parsed() == {"clauses": []}
    assert r.model == "muse-spark-1.3" and r.attempts == 1
    assert r.request_sha256 == request_sha256(body)


async def test_wrong_response_model_raises(tmp_path: Path) -> None:
    client = replay(tmp_path)
    body = client.build_respond_body("judge", "x", effort="minimal")
    write_respond_fixture(tmp_path, body, responses_events(text="{}", model="some-other-model"))
    with pytest.raises(ModelMismatch):
        await client.respond("judge", "x", effort="minimal", lane="l0")


async def test_incomplete_retries_once_at_double_cap(tmp_path: Path) -> None:
    client = replay(tmp_path)
    first = client.build_respond_body("guide", "hi")
    second = client.build_respond_body("guide", "hi", attempt=2)
    assert second["max_output_tokens"] == 2 * first["max_output_tokens"]
    write_respond_fixture(tmp_path, first, responses_events(status="incomplete"))
    write_respond_fixture(tmp_path, second, responses_events(text="hello"))
    r = await client.respond("guide", "hi", lane="l0")
    assert r.text == "hello" and r.attempts == 2
    assert [b["max_output_tokens"] for b in client.requests] == [first["max_output_tokens"],
                                                                  second["max_output_tokens"]]


def _split_deltas(events: list, pieces: list[str]) -> list:
    """The helper's one text delta, cut into several, as a real stream sends them."""
    i = next(n for n, (_, ev) in enumerate(events) if ev["type"] == "response.output_text.delta")
    t, ev = events[i]
    return events[:i] + [(t, {**ev, "delta": p}) for p in pieces] + events[i + 1:]


async def test_on_delta_sees_each_text_delta_and_a_retry_restarts(tmp_path: Path) -> None:
    client = replay(tmp_path)
    first = client.build_respond_body("guide", "hi")
    second = client.build_respond_body("guide", "hi", attempt=2)
    cut = responses_events(text="xx", status="completed")
    partial = [e for e in _split_deltas(cut, ['{"jobs": [', '{"id"'])
               if e[1]["type"] != "response.completed"] + responses_events(status="incomplete")[-1:]
    write_respond_fixture(tmp_path, first, partial)
    write_respond_fixture(tmp_path, second, _split_deltas(responses_events(text='{"jobs": []}'),
                                                          ['{"jobs"', ': []}']))
    seen: list[tuple[str, int]] = []
    r = await client.respond("guide", "hi", lane="l0", on_delta=lambda t, a: seen.append((t, a)))
    assert seen == [('{"jobs": [', 1), ('{"id"', 1), ('{"jobs"', 2), (": []}", 2)]
    assert r.text == '{"jobs": []}' and r.attempts == 2 and r.delta_error is None
    assert client.requests == [first, second]    # the hook changes no body, so no sha


async def test_a_failing_on_delta_never_fails_the_call(tmp_path: Path) -> None:
    client = replay(tmp_path)
    body = client.build_respond_body("guide", "hi")
    write_respond_fixture(tmp_path, body, _split_deltas(responses_events(text="abc"), ["a", "b", "c"]))
    calls_seen: list[str] = []

    def boom(t: str, _attempt: int) -> None:
        calls_seen.append(t)
        raise ValueError("bad parse")

    r = await client.respond("guide", "hi", lane="l0", on_delta=boom)
    assert r.text == "abc" and calls_seen == ["a"] and r.delta_error == "ValueError: bad parse"


async def test_second_incomplete_raises_and_is_not_parsed(tmp_path: Path) -> None:
    client = replay(tmp_path)
    for attempt in (1, 2):
        body = client.build_respond_body("guide", "hi", attempt=attempt)
        write_respond_fixture(tmp_path, body, responses_events(status="incomplete"))
    with pytest.raises(IncompleteResponse):
        await client.respond("guide", "hi", lane="l0")
    assert len(client.requests) == 2  # once, then once more; never a third time


async def test_missing_fixture_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(MissingFixture):
        await replay(tmp_path).respond("judge", "never recorded", lane="l0")


async def test_past_budget_call_is_refused_before_the_network(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl0 = 0.01\n")
    (private / "ledger.jsonl").write_text(json.dumps({"lane": "l0", "usd": 0.0099}) + "\n")
    backend = ReplayBackend([tmp_path])
    client = Client("live", load_config(), backend, ledger=Ledger(private / "ledger.jsonl", private / "budgets.toml"),
                    fixtures_root=tmp_path, private=private)
    with pytest.raises(BudgetExceeded):
        await client.respond("guide", "hi", lane="l0")
    with pytest.raises(BudgetExceeded):
        await client.respond("guide", "hi", lane="no-such-lane")
    assert backend.calls == 0 and client.requests == []
    assert not (private / "requests").exists()


async def test_total_cap_counts_every_line(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 1.0\n[lanes]\nl0 = 5.0\nprobe = 1.0\n")
    (private / "ledger.jsonl").write_text(json.dumps({"lane": "probe", "usd": 0.9999}) + "\n")
    ledger = Ledger(private / "ledger.jsonl", private / "budgets.toml")
    with pytest.raises(BudgetExceeded, match="cap"):
        ledger.check("l0", 0.01)


async def test_live_mode_writes_a_ledger_line_with_store_false(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl0 = 2.0\n")
    cfg = load_config()
    replay_client = calls.make_client("replay", fixtures_root=tmp_path, private=private)
    body = replay_client.build_respond_body("judge", "x", effort="minimal")
    write_respond_fixture(tmp_path, body, responses_events(text="{}"))
    client = Client("live", cfg, ReplayBackend([tmp_path]), ledger=Ledger(private / "ledger.jsonl",
                    private / "budgets.toml"), fixtures_root=tmp_path, private=private)
    await client.respond("judge", "x", effort="minimal", lane="l0")
    (line,) = [json.loads(x) for x in (private / "ledger.jsonl").read_text().splitlines()]
    assert line["store"] is False and line["lane"] == "l0" and line["usd"] > 0
    assert line["response_model"] == "muse-spark-1.3"
    assert not (private / "requests").exists()  # a live call keeps no body, only its hash


async def test_record_mode_keeps_the_body_beside_its_fixture_0600(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl0 = 2.0\n")
    body = calls.make_client("replay", fixtures_root=tmp_path, private=private).build_respond_body(
        "judge", "x", effort="minimal")
    write_respond_fixture(tmp_path, body, responses_events(text="{}"))
    client = Client("record", load_config(), ReplayBackend([tmp_path]), ledger=Ledger(private / "ledger.jsonl",
                    private / "budgets.toml"), fixtures_root=tmp_path / "out", private=private)
    await client.respond("judge", "x", effort="minimal", lane="l0")
    saved = private / "requests" / f"{request_sha256(body)}.json"
    assert json.loads(saved.read_text()) == body
    assert saved.stat().st_mode & 0o777 == 0o600


async def test_run_tag_fills_the_ledger_run_and_the_inner_scope_wins(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl0 = 2.0\n")
    body = calls.make_client("replay", fixtures_root=tmp_path, private=private).build_respond_body(
        "judge", "x", effort="minimal")
    write_respond_fixture(tmp_path, body, responses_events(text="{}"))
    client = Client("live", load_config(), ReplayBackend([tmp_path]), ledger=Ledger(private / "ledger.jsonl",
                    private / "budgets.toml"), fixtures_root=tmp_path, private=private)

    async def call() -> None:
        await client.respond("judge", "x", effort="minimal", lane="l0")

    await call()
    with calls.run_tag("add-appliance:acme/WM-0000"):
        await call()
        with calls.run_tag("card-build:acme/WM-0000"):
            await asyncio.gather(call(), asyncio.create_task(call()))
        await call()
    await call()
    runs = [json.loads(x)["run"] for x in (private / "ledger.jsonl").read_text().splitlines()]
    assert runs == [None, "add-appliance:acme/WM-0000", "card-build:acme/WM-0000", "card-build:acme/WM-0000",
                    "add-appliance:acme/WM-0000", None]


async def test_every_request_has_store_false_and_images_at_most_1024(tmp_path: Path) -> None:
    client = replay(tmp_path)
    big = jpeg(3000, 2000)
    body = client.build_respond_body("locate", "where is the filter?", images=[big])
    write_respond_fixture(tmp_path, body, responses_events(text="{}"))
    await client.respond("locate", "where is the filter?", images=[big], lane="l0")

    sam_body_events = sam_events([FakeObject(1, (10, 20, 49, 59))], 1024, 683)
    frame = jpeg(2048, 1366)
    im = client._prepare([frame])[0]
    sam_body = client._sam_body(client.config.role("sam"), "debris filter",
                                {"type": "input_image", "image_url": im.data_url()})
    write_respond_fixture(tmp_path, sam_body, sam_body_events, kind="sam_image")
    await client.sam_image(frame, "debris filter", lane="l0")

    assert len(client.requests) == 2
    for sent in client.requests:
        assert sent["store"] is False and sent["stream"] is True
        for item in sent["input"]:
            for part in item.get("content", []):
                if part.get("type") == "input_image":
                    raw = calls.images.base64.b64decode(part["image_url"].split(",", 1)[1])
                    assert max(Image.open(io.BytesIO(raw)).size) <= 1024


async def test_sam_parses_and_caches_by_frame_hash_and_prompt(tmp_path: Path) -> None:
    client = replay(tmp_path)
    frame = jpeg(800, 600)
    im = client._prepare([frame])[0]
    body = client._sam_body(client.config.role("sam"), "debris filter",
                            {"type": "input_image", "image_url": im.data_url()})
    write_respond_fixture(tmp_path, body, sam_events([FakeObject(1, (100, 150, 299, 349), 0.87)], 800, 600),
                          kind="sam_image")
    first = await client.sam_image(frame, "debris filter", lane="l0", frame_hash="f1")
    assert not first.cached and len(first.objects) == 1
    obj = first.objects[0]
    assert obj.conf == pytest.approx(0.87)
    assert obj.mask_svg
    assert first.box_1000(obj)[0] == pytest.approx(125, abs=2)
    second = await client.sam_image(frame, "debris filter", lane="l0", frame_hash="f1")
    assert second.cached and second.objects == first.objects
    assert isinstance(client.backend, ReplayBackend) and client.backend.calls == 1


async def test_sam_box_prompt_uses_pixel_grammar(tmp_path: Path) -> None:
    client = replay(tmp_path)
    text = client._sam_prompt((100, 200, 500, 600), 1000, 500)
    assert text == "<0f>1<|box;x1=100;y1=100;x2=500;y2=300;w=1000;h=500|>"
    with pytest.raises(ValueError):
        client._sam_prompt("open the filter and unscrew it " * 3, 1000, 500)


async def test_transcribe_replays(tmp_path: Path) -> None:
    client = replay(tmp_path)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)
    audio = buf.getvalue()

    request = {"mode": "PUSH_TO_TALK", "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV",
               "languageBias": ["Dutch", "English"], "keywords": []}
    sha = request_sha256({"request": request, "audio_sha256": hashlib.sha256(audio).hexdigest()})
    write_fixture(tmp_path, "test", sha, kind="transcribe", role="transcribe", params={}, response_model=None,
                  events=[(400.0, {"type": "asr.response", "body": {"transcript": "het filter zit vast",
                                                                     "model": "muse-voice-transcribe-1.0"}})],
                  latency_ms=400.0, usd=0.0)
    t = await client.transcribe(audio, lane="l0")
    assert t.text == "het filter zit vast"
    with pytest.raises(ValueError):
        await client.transcribe(b"not a wav at all" * 10, lane="l0")


@pytest.mark.parametrize(("named", "ok"), [(None, True), ("muse-voice-transcribe-1.0", True),
                                            ("muse-spark-1.3", False)])
async def test_transcribe_asserts_the_model_whenever_the_reply_names_one(tmp_path: Path, named: str | None,
                                                                          ok: bool) -> None:
    client = replay(tmp_path)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)
    audio = buf.getvalue()
    request = {"mode": "PUSH_TO_TALK", "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV",
               "languageBias": ["Dutch", "English"], "keywords": []}
    sha = request_sha256({"request": request, "audio_sha256": hashlib.sha256(audio).hexdigest()})
    reply = {"transcript": "het filter zit vast"} | ({} if named is None else {"model": named})
    write_fixture(tmp_path, "test", sha, kind="transcribe", role="transcribe", params={}, response_model=named,
                  events=[(400.0, {"type": "asr.response", "body": reply})], latency_ms=400.0, usd=0.0)
    if ok:
        assert (await client.transcribe(audio, lane="l0")).text == "het filter zit vast"
    else:
        with pytest.raises(ModelMismatch):
            await client.transcribe(audio, lane="l0")


def test_contributor_model_id_is_refused_at_config_load(tmp_path: Path) -> None:
    src = Path(calls.config.DEFAULT_CONFIG).read_text()
    bad = tmp_path / "models.toml"
    bad.write_text(src.replace('"muse-spark-1.3"', '"muse-spark-1.3' + "-" + 'contributor"', 1))
    with pytest.raises(ConfigError):
        load_config(bad)
    cfg = load_config()
    roles = dict(cfg.roles)
    roles["judge"] = replace(roles["judge"], model="muse-spark-1.3" + "-" + "contributor")
    with pytest.raises(ConfigError):
        Client("replay", replace(cfg, roles=roles), ReplayBackend([tmp_path]), ledger=None, fixtures_root=tmp_path,
               private=None)


def test_live_mode_needs_a_ledger(tmp_path: Path) -> None:
    with pytest.raises(calls.errors.CallFailed):
        Client("live", load_config(), ReplayBackend([tmp_path]), ledger=None, fixtures_root=tmp_path, private=None)


def test_fixture_has_no_request_echo(tmp_path: Path) -> None:
    client = replay(tmp_path)
    body = client.build_respond_body("judge", "secret prompt words", effort="minimal")
    events = responses_events(text="{}")
    events[-1][1]["response"]["instructions"] = "secret prompt words"
    events[-1][1]["response"]["input"] = [{"text": "secret prompt words"}]
    write_respond_fixture(tmp_path, body, events)
    (path,) = tmp_path.glob("test/*.jsonl")
    assert "secret prompt words" not in path.read_text()


def test_key_is_never_read_from_env_or_argv() -> None:
    src = (Path(calls.__file__).parent / "backends" / "meta.py").read_text()
    assert "os.environ" not in src and "getenv" not in src and "sys.argv" not in src


async def test_guide_asks_for_a_reasoning_summary_and_surfaces_it(tmp_path: Path) -> None:
    client = replay(tmp_path)
    body = client.build_respond_body("guide", "hi")
    assert body["reasoning"] == {"effort": "low", "summary": "auto"}
    assert "summary" not in client.build_respond_body("judge", "x")["reasoning"]
    events = responses_events(text="ok")
    events[-1][1]["response"]["output"][0]["summary"] = [{"type": "summary_text", "text": "Checking the filter."}]
    write_respond_fixture(tmp_path, body, events)
    r = await client.respond("guide", "hi", lane="l0")
    assert r.reasoning_summary == "Checking the filter."


def test_explicit_cap_lowers_the_role_default_but_not_the_floor(tmp_path: Path) -> None:
    client = replay(tmp_path)
    assert client.build_respond_body("appliance_file", "x", max_output_tokens=12000)["max_output_tokens"] == 12000
    assert client.build_respond_body("guide", "x", max_output_tokens=100)["max_output_tokens"] == 4000


async def test_count_tokens_replays(tmp_path: Path) -> None:
    client = replay(tmp_path)
    full = client.build_respond_body("guide", "count me", images=[jpeg(1600, 1200)])
    body = {k: full[k] for k in ("model", "input")}
    write_fixture(tmp_path, "test", request_sha256(body), kind="count_tokens", role="guide", params={},
                  events=[(120.0, {"type": "input_tokens.response", "body": {"input_tokens": 1042}})],
                  response_model=None, latency_ms=120.0, usd=0.0)
    assert await client.count_tokens("guide", "count me", images=[jpeg(1600, 1200)], lane="l0") == 1042


def test_household_daily_cap(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl1 = 10.0\n")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    (private / "ledger.jsonl").write_text(
        json.dumps({"lane": "l1", "usd": 0.40, "household": "h1", "ts": now}) + "\n"
        + json.dumps({"lane": "l1", "usd": 0.40, "household": "h1", "ts": "2026-01-01T00:00:00+00:00"}) + "\n"
    )
    ledger = Ledger(private / "ledger.jsonl", private / "budgets.toml")
    ledger.check("l1", 0.04, household="h1", household_cap_usd=0.45)
    with pytest.raises(BudgetExceeded, match="household h1"):
        ledger.check("l1", 0.06, household="h1", household_cap_usd=0.45)
    ledger.check("l1", 0.06, household="h2", household_cap_usd=0.45)
    ledger.check("l1", 0.06, household="h1")  # no cap passed: the lane budget alone


async def test_household_cap_refuses_before_the_network(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl1 = 10.0\n")
    backend = ReplayBackend([tmp_path])
    client = Client("live", load_config(), backend, ledger=Ledger(private / "ledger.jsonl", private / "budgets.toml"),
                    fixtures_root=tmp_path, private=private)
    with pytest.raises(BudgetExceeded):
        await client.respond("guide", "hi", lane="l1", household="h1", household_cap_usd=0.0)
    assert backend.calls == 0


class _Hang:
    """A backend that never answers, so every call times out."""

    name = "replay"

    async def stream(self, body: dict, *, sha: str, kind: str):  # type: ignore[no-untyped-def]
        await asyncio.sleep(10)
        yield 0.0, {}

    async def transcribe(self, request: dict, wav: bytes, *, sha: str):  # type: ignore[no-untyped-def]
        await asyncio.sleep(10)


async def test_timeout_carries_the_ledger_estimate(tmp_path: Path) -> None:
    client = replay(tmp_path)
    client.backend = _Hang()  # type: ignore[assignment]
    with pytest.raises(CallTimeout) as e:
        await client.respond("judge", "is the filter out?", effort="minimal", lane="l0", timeout_s=0.05)
    assert e.value.usd > 0
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)
    with pytest.raises(CallTimeout) as e:
        await client.transcribe(buf.getvalue(), lane="l0", timeout_s=0.05)
    assert e.value.usd > 0
    assert CallTimeout("x").usd == 0.0


def test_forget_household_keeps_every_line_and_its_spend(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl0 = 5.0\n")
    ledger = Ledger(private / "ledger.jsonl", private / "budgets.toml")
    gone, kept = "hh_" + "a" * 20, "hh_" + "b" * 20
    for i, hh in enumerate((gone, kept, gone, None)):
        ledger.append({"lane": "l0", "usd": 0.25, "household": hh, "session_id": f"s{i}" if hh else None,
                       "model": "m", "status": "completed", "mode": "live"})
    before = ledger.totals()
    size = (private / "ledger.jsonl").stat().st_size
    assert ledger.forget_household(gone) == 2
    assert ledger.forget_household(gone) == 0
    rows = [json.loads(x) for x in (private / "ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 4 and [r["usd"] for r in rows] == [0.25] * 4
    assert [r["household"] for r in rows] == [None, kept, None, None]
    assert [r["session_id"] for r in rows] == [None, "s1", None, None]
    assert ledger.totals() == before and (private / "ledger.jsonl").stat().st_size == size   # rewritten in place
    ledger.append({"lane": "l0", "usd": 0.5, "household": "h", "session_id": "s", "model": "m",
                   "status": "completed", "mode": "live"})
    assert ledger.forget_household("h") == 1                     # a one-letter id grows the line: whole rewrite
    rows = [json.loads(x) for x in (private / "ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 5 and rows[-1]["household"] is None and ledger.totals().total == 1.5
    with pytest.raises(ValueError):
        ledger.forget_household("")
