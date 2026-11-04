"""The plate over the WS: plate_photo is read from memory and never written; plate_read carries the uncertain
characters; a confirm_plate tap puts the user's choices in and sends a settled plate_read (nothing uncertain), whose
brand and model alone are kept (a home's in plates/homes/<household>/). In record mode the read's request body and
response are dropped once it is in. A re-run plays the recorded plate_read back with no call."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import calls
from app import adding
from tests.l1.test_session import session


@dataclass
class Plate:
    brand: str | None
    model: str | None
    fields: list[dict[str, Any]]
    reason: str = "amber"

    def ws_fields(self) -> list[dict[str, Any]]:
        return self.fields


@dataclass
class Reader:
    card: Plate | None = None
    fail: Exception | None = None
    seen: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, photo: bytes, **kw: Any) -> Plate:
        self.seen.append({"photo": photo, **kw})
        if self.fail is not None:
            raise self.fail
        assert self.card is not None
        return self.card


AMBER = Plate("bosch", "SMV4HAX4ON", [
    {"name": "brand", "value": "bosch", "uncertain": []},
    {"name": "model", "value": "SMV4HAX4ON", "uncertain": [{"index": 8, "options": ["O", "0"]}]}])


def photo(seq: int = 1) -> dict[str, Any]:
    return {"type": "plate_photo", "seq": seq, "t_ms": 0, "photo_id": "pl_1",
            "jpeg": base64.b64encode(b"\xff\xd8plate").decode()}


def reads(s: Any) -> list[dict[str, Any]]:
    return [m for m in s.out if m["type"] == "plate_read"]


async def test_plate_photo_is_kept_read_and_settled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    s = session(tmp_path)
    s.start()
    s.plate_reader = r = Reader(AMBER)
    await s.handle(photo())
    await s.idle()
    assert r.seen[0]["photo"] == b"\xff\xd8plate"
    assert not list((tmp_path / "private").rglob("*.jpg"))  # the photo is never written
    assert r.seen[0]["session_id"] == "t1"
    first = reads(s)[-1]
    assert first["model"] == "SMV4HAX4ON" and first["fields"][1]["uncertain"]
    await s.handle({"type": "tap", "seq": 2, "t_ms": 0, "action": "confirm_plate", "arg": json.dumps({"model": ["0"]})})
    await s.idle()
    settled = reads(s)[-1]
    assert settled["model"] == "SMV4HAX40N" and settled["brand"] == "bosch" and settled["photo_id"] == "pl_1"
    assert all(f["uncertain"] == [] for f in settled["fields"])
    assert adding.settled_plate("pl_1") == {"brand": "bosch", "model": "SMV4HAX40N"}


async def test_a_homes_plate_is_kept_in_its_own_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    s = session(tmp_path, household="h_1")
    s.start()
    s.plate_reader = Reader(Plate("bosch", "SMV4HAX40N", AMBER.fields[:1]))
    await s.handle(photo())
    await s.idle()
    home = tmp_path / "private" / "plates" / "homes" / "h_1"
    assert sorted(p.name for p in home.iterdir()) == ["pl_1.json"]  # brand and model only; deleting the home deletes it
    assert json.loads((home / "pl_1.json").read_text()) == {"brand": "bosch", "model": "SMV4HAX40N"}
    assert adding.settled_plate("pl_1", "h_1") is not None and adding.settled_plate("pl_1", "h_2") is None


async def test_a_choice_that_is_not_an_option_stays_uncertain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    s = session(tmp_path)
    s.start()
    s.plate_reader = Reader(AMBER)
    await s.handle(photo())
    await s.idle()
    await s.handle({"type": "tap", "seq": 2, "t_ms": 0, "action": "confirm_plate", "arg": json.dumps({"model": ["Q"]})})
    await s.idle()
    again = reads(s)[-1]
    assert again["model"] == "SMV4HAX4ON" and again["fields"][1]["uncertain"] == [{"index": 8, "options": ["O", "0"]}]
    assert adding.settled_plate("pl_1") is None  # not settled: POST /appliances reads the plate again


async def test_a_failed_read_says_so_and_sends_an_empty_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    s = session(tmp_path)
    s.start()
    s.plate_reader = Reader(fail=calls.CallTimeout("plate: 6 s"))
    await s.handle(photo())
    await s.idle()
    assert reads(s)[-1] == {**reads(s)[-1], "brand": None, "model": None, "fields": []}
    assert any(m["type"] == "notice" and m["kind"] == "slow_connection" for m in s.out)


async def test_a_replay_without_the_plate_recording_is_not_a_lookup_failure(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    s = session(tmp_path)
    s.start()
    s.plate_reader = Reader(fail=calls.MissingFixture("no fixture for request"))
    await s.handle(photo())
    await s.idle()
    assert reads(s)[-1]["fields"] == []
    assert [m["kind"] for m in s.out if m["type"] == "notice"] == ["no_recording"]
    rows = [m for m in s.out if m["type"] == "rail" and m["tool"] == "plate"]
    assert rows and rows[0]["result_short"] == "no recording for this call" and rows[0]["args_short"] == ""


async def test_a_session_with_a_plate_photo_re_runs_from_the_recorded_read(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from app import plates, recorder, rerun

    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    reader = Reader(card=AMBER)
    monkeypatch.setattr(plates, "_default_reader", lambda: reader)
    s = session(tmp_path)
    s.start()
    await s.handle(photo())
    await s.idle()
    await s.close()
    live = [m for m in s.out if m["type"] in ("rail", "plate_read")]
    r = await rerun.rerun(recorder.load(tmp_path, "t1"), s.client, guide=False)
    assert len(reader.seen) == 1  # the re-run made no call: there is no photo to read
    assert [m["fields"] for m in r.again if m["type"] == "plate_read"] == [AMBER.fields]
    again = [m for m in r.again if m["type"] in ("rail", "plate_read")]
    assert [{k: v for k, v in m.items() if k not in ("seq", "t_ms")} for m in again] == \
        [{k: v for k, v in m.items() if k not in ("seq", "t_ms")} for m in live if m["type"] == "plate_read"
         or m["tool"] == "plate"]
    assert r.same, r.diff()[:3]


async def test_a_re_run_with_no_recorded_read_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import recorder, rerun

    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    s = session(tmp_path)
    s.start()
    s.rec.client(s.clock(), "c2s", photo())  # a record cut short: the photo, no read
    await s.close()
    r = await rerun.rerun(recorder.load(tmp_path, "t1"), s.client, guide=False)
    assert [m["kind"] for m in r.again if m["type"] == "notice"] == ["no_recording"]
    assert [m["fields"] for m in r.again if m["type"] == "plate_read"] == [[]]


class Recording:
    """A record-mode client: respond keeps the body under private/requests and the response under fixtures/l1, as
    calls.Client does, and returns the plate's answer."""

    mode = "record"

    def __init__(self, root: Path) -> None:
        self.private, self.fixtures_root = root / "private", root / "private" / "fixtures"

    def build_respond_body(self, role: str, input: Any, *, attempt: int = 1, **kw: Any) -> dict[str, Any]:
        return {"role": role, "input": input, "attempt": attempt,
                "images": [base64.b64encode(b).decode() for b in kw.get("images", ())]}

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        from calls.fixtures import request_sha256, save_request_body, write_fixture

        body = self.build_respond_body(role, input, **{k: v for k, v in kw.items() if k == "images"})
        sha = request_sha256(body)
        save_request_body(self.private, sha, body)
        write_fixture(self.fixtures_root, "l1", sha, kind="respond", role=role, params={}, events=[],
                      response_model="m", latency_ms=1.0, usd=0.0)
        out = {"plate_visible": True, "brand": "Bosch", "model_code": "SMV4HAX40N"}
        return calls.Response(role, "m", "completed", json.dumps(out), [], [], calls.Usage(), 1.0, None, 0.0, sha, 1,
                              "c")


async def test_record_mode_drops_the_plate_calls_body_and_response(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """The request body holds the photo and the response what the plate says: neither stays once the read is in."""
    from ingest.plate import read_plate

    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    c = Recording(tmp_path)
    s = session(tmp_path, c)
    s.start()
    s.plate_reader = read_plate
    await s.handle(photo())
    await s.idle()
    assert reads(s)[-1]["model"] == "SMV4HAX40N"
    assert not list((tmp_path / "private" / "requests").glob("*.json"))
    assert not list((tmp_path / "private" / "fixtures").rglob("*.jsonl"))
    assert not list((tmp_path / "private").rglob("*.jpg"))
    await s.close()


async def test_a_failed_read_drops_its_body_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import plates

    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    c = Recording(tmp_path)

    async def failing(photo: bytes, *, client: Any, **kw: Any) -> Any:
        await client.respond("plate", "read it", images=[photo], lane="l1")
        raise calls.CallTimeout("plate: 6 s")

    s = session(tmp_path, c)
    s.start()
    await plates.on_plate_photo(s, plates.ws.PlatePhoto.model_validate(photo()), failing)
    assert not list((tmp_path / "private" / "requests").glob("*.json"))
    assert not list((tmp_path / "private" / "fixtures").rglob("*.jsonl"))
    await s.close()


def test_outside_record_mode_nothing_is_deleted(tmp_path: Path) -> None:
    from app import plates

    kept = tmp_path / "private" / "requests" / "abc.json"
    kept.parent.mkdir(parents=True)
    kept.write_text("{}")
    c = Recording(tmp_path)
    c.mode = "live"  # type: ignore[misc]
    assert plates.forget(c, ["abc"]) == 0 and kept.exists()


async def test_the_noted_hash_is_the_one_the_real_client_keys_on(tmp_path: Path) -> None:
    """Noted builds each attempt's body with the client's own build_respond_body, so attempt 1's hash is the one
    record mode files the body and response under (calls.Client, replaying a synthetic recording of the read)."""
    from app import plates
    from calls.testing import responses_events, write_respond_fixture
    from ingest.plate import PLATE_FORMAT, PLATE_PROMPT
    from tests.l1.helpers import jpeg

    client = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    shot = jpeg()
    body = client.build_respond_body("plate", PLATE_PROMPT, images=[shot], format=PLATE_FORMAT)
    said = json.dumps({"plate_visible": True, "brand": "Bosch", "model_code": "SMV4HAX40N"})
    write_respond_fixture(tmp_path, body, responses_events(text=said, model=client.config.role("plate").model))
    noted = plates.Noted(client)
    r = await noted.respond("plate", PLATE_PROMPT, images=[shot], format=PLATE_FORMAT, lane="l1")
    assert noted.shas[0] == r.request_sha256
