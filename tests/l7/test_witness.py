"""The no-SAM witness: optical flow over synthetic frames, the decision table, and the SAM-or-track choice."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import cv2
import httpx
import numpy as np
import pytest

from calls import load_config
from calls.backends import local as backend
from local import witness as w

REAL_PENDING = backend.sam_weights_pending
W, H = 640, 480


def scene(seed: int = 7) -> np.ndarray[Any, Any]:
    rng = np.random.default_rng(seed)
    im = cv2.GaussianBlur((rng.random((H + 80, W + 80)) * 255).astype(np.uint8), (0, 0), 2.0)
    return cv2.normalize(im, None, 0, 255, cv2.NORM_MINMAX)


def clip(shifts: list[tuple[int, int]], seed: int = 7) -> list[bytes]:
    big = scene(seed)
    out = []
    for dx, dy in shifts:
        crop = big[40 + dy:40 + dy + H, 40 + dx:40 + dx + W]
        out.append(cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes())
    return out


PLACE = (400, 400, 600, 600)


def test_flow_follows_a_steady_view() -> None:
    t = w.flow_track(clip([(0, 0)] * 8), PLACE)
    assert t.held == 1.0 and t.drift <= 4


def test_flow_follows_a_slow_pan() -> None:
    # the camera pans 3 px a frame to the right: the scene, and the place, move left
    t = w.flow_track(clip([(3 * i, 0) for i in range(8)]), PLACE)
    assert t.held == 1.0
    moved = (t.boxes[0][0] + t.boxes[0][2]) / 2 - (t.boxes[-1][0] + t.boxes[-1][2]) / 2
    assert abs(moved - 21 * 1000 / W) <= 5


def test_flow_loses_a_cut() -> None:
    frames = clip([(0, 0)] * 4) + clip([(0, 0)] * 4, seed=99)
    t = w.flow_track(frames, PLACE)
    assert t.held < w.PRESENT_FRACTION


def test_flow_needs_texture() -> None:
    flat = cv2.imencode(".jpg", np.full((H, W), 128, np.uint8))[1].tobytes()
    assert w.flow_track([flat] * 5, PLACE).boxes == []


STEADY = w.Track([PLACE] * 10, 10)


@pytest.mark.parametrize("expect,track,located,result", [
    ("present", STEADY, (420, 420, 580, 580), "done"),
    ("present", STEADY, (0, 0, 150, 150), "not_done"),
    ("present", STEADY, None, "cant_see"),
    ("present", w.Track([PLACE] * 5, 10), (420, 420, 580, 580), "cant_see"),
    ("present", w.Track([PLACE, (500, 400, 700, 600)], 2), (420, 420, 580, 580), "cant_see"),
    ("absent", STEADY, None, "done"),
    ("absent", STEADY, (0, 0, 150, 150), "done"),
    ("absent", STEADY, (420, 420, 580, 580), "not_done"),
    ("absent", w.Track([], 10), None, "cant_see"),
])
def test_decide(expect: Any, track: w.Track, located: Any, result: str) -> None:
    assert w.decide(expect, track, located)[0] == result


def test_located_rejects_bad_boxes() -> None:
    assert w._located(json.dumps({"found": True, "box": [1, 2, 3, 4]})) == (1, 2, 3, 4)
    assert w._located(json.dumps({"found": True, "box": [3, 2, 1, 4]})) is None
    assert w._located(json.dumps({"found": False, "box": [1, 2, 3, 4]})) is None
    assert w._located("not json") is None


class FakeModels:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, Any]] = []

    async def respond(self, role: str, input: Any, **kw: Any) -> tuple[Any, float]:
        self.calls.append((role, kw.get("format")))
        return SimpleNamespace(text=self.text), 0.0


def test_track_witness_end_to_end() -> None:
    pytest.importorskip("app.verdict")
    frames = [SimpleNamespace(jpeg=j) for j in clip([(i, 0) for i in range(8)])]
    models = FakeModels(json.dumps({"found": True, "box": [410, 410, 590, 590]}))
    spec = SimpleNamespace(expect="present")
    place = SimpleNamespace(box=PLACE)
    out = asyncio.run(w.track_witness(models, frames, frames[-1], spec, None, place, "lint filter", None))  # type: ignore[arg-type]
    assert (out.ran, out.result) == ("track", "done")
    assert out.reason.startswith(w.TRACK_LABEL)
    assert models.calls == [("locate", w.LOCATE_FORMAT)]


@pytest.fixture(autouse=True)
def _weights_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    """The health tests below are about the server's answers, so local SAM is on (eval's setting); the no-weights
    case and the default (SAM off beside Glimmer) are tested apart."""
    monkeypatch.setattr(backend, "sam_weights_pending", lambda: False)
    monkeypatch.setenv("FIXIT_LOCAL_SAM", "1")
    monkeypatch.delenv("FIXIT_ENGINE", raising=False)  # a bare ollama load; the register path is tested apart


def _sam_health(weights: bool, loaded: bool = False) -> httpx.MockTransport:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json={"ok": weights, "weights": weights, "loaded": loaded})
        if req.url.path in ("/api/ps", "/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "muse-glimmer:30b"}]})
        return httpx.Response(404)
    return httpx.MockTransport(h)


@pytest.mark.parametrize("weights", [True, False])
def test_weights_decide_the_witness(weights: bool) -> None:
    cfg = load_config()
    client = SimpleNamespace(backend=SimpleNamespace(sam_url=cfg.local_sam_url, transport=_sam_health(weights)))
    assert asyncio.run(w.sam_weights_served(client)) is weights


def test_no_sam_url_means_track() -> None:
    client = SimpleNamespace(backend=SimpleNamespace(sam_url=""))
    assert asyncio.run(w.sam_weights_served(client)) is False


def test_health_reports_glimmer_and_sam() -> None:
    cfg = load_config()
    ok, detail = asyncio.run(backend.health(cfg, transport=_sam_health(True, loaded=True)))
    assert ok and "Muse Glimmer 30B (local): loaded and ready" in detail and "Local SAM 3.1: loaded and ready" in detail
    ok, detail = asyncio.run(backend.health(cfg, transport=_sam_health(False)))
    assert ok and "Local SAM 3.1: no weights yet" in detail and "motion tracking follows its place (no SAM)" in detail


def test_health_sam_down_keeps_glimmer_ok() -> None:
    cfg = load_config()
    sam_host = httpx.URL(cfg.local_sam_url).host

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.host == sam_host and req.url.path == "/health" and req.url.port == httpx.URL(cfg.local_sam_url).port:
            raise httpx.ConnectError("All connection attempts failed", request=req)
        return httpx.Response(200, json={"models": [{"name": "muse-glimmer:30b"}]})

    ok, detail = asyncio.run(backend.health(cfg, transport=httpx.MockTransport(h)))
    assert ok and "Local SAM 3.1: can't be reached (ConnectError)" in detail and "(no SAM)" in detail


def test_health_never_raises() -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    ok, detail = asyncio.run(backend.health(load_config(), transport=httpx.MockTransport(down)))
    assert not ok and detail.startswith("Muse Glimmer 30B (local): can't be reached (ConnectError)")
    assert "Local SAM 3.1: " in detail


def _ollama(installed: bool, loaded: bool) -> httpx.MockTransport:
    glimmer = [{"name": "muse-glimmer:30b"}]

    def h(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/api/tags":
            return httpx.Response(200, json={"models": glimmer if installed else []})
        if r.url.path == "/api/ps":
            return httpx.Response(200, json={"models": glimmer if loaded else []})
        if r.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True, "load_duration": 14_200_000_000})
        if r.url.path == "/warm":
            return httpx.Response(200, json={"ok": True, "loaded": True, "ms": 16600})
        return httpx.Response(200, json={})
    return httpx.MockTransport(h)


def test_health_installed_not_loaded_is_ok() -> None:
    ok, detail = asyncio.run(backend.health(load_config(), transport=_ollama(True, False)))
    assert ok and "installed, loads on first use" in detail


def test_health_not_installed_is_red() -> None:
    ok, detail = asyncio.run(backend.health(load_config(), transport=_ollama(False, False)))
    assert not ok and "not installed" in detail


def test_warm_up_reports_the_load_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "_last_load_s", None)
    ok, detail = asyncio.run(backend.warm_up(load_config(), transport=_ollama(True, False)))
    assert ok and detail == "Muse Glimmer 30B (local) in 14.2 s"
    ok, detail = asyncio.run(backend.health(load_config(), transport=_ollama(True, False)))
    assert ok and "loads on first use (~14 s)" in detail


def test_provider_picks_and_caches() -> None:
    from app.witness import sam_witness

    cfg = load_config()
    for weights, want in [(True, sam_witness), (False, w.track_witness)]:
        client = SimpleNamespace(backend=SimpleNamespace(sam_url=cfg.local_sam_url, transport=_sam_health(weights)))
        assert asyncio.run(w.provider(client)) is want
        client.backend.transport = _sam_health(not weights)
        assert asyncio.run(w.provider(client)) is want  # probed once per backend


def test_provider_without_a_backend() -> None:
    assert asyncio.run(w.provider(object())) is w.track_witness
    assert asyncio.run(w.sam_weights_served(object())) is False


def test_sam_weights_pending(tmp_path: Path) -> None:
    token, weights = tmp_path / "hf-read.token", tmp_path / "sam3.1"
    assert REAL_PENDING(token, weights)  # neither yet
    token.touch(mode=0o600)
    assert REAL_PENDING(token, weights)  # token, no weights dir
    weights.mkdir()
    assert REAL_PENDING(token, weights)  # empty dir
    (weights / "sam3.1_multiplex.pt").touch()
    assert not REAL_PENDING(token, weights)


def test_health_says_access_pending_without_asking_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "sam_weights_pending", lambda: True)
    asked: list[str] = []

    def h(r: httpx.Request) -> httpx.Response:
        asked.append(r.url.path)
        return httpx.Response(200, json={"models": [{"name": "muse-glimmer:30b"}]})

    ok, detail = asyncio.run(backend.health(load_config(), transport=httpx.MockTransport(h)))
    assert ok and "Local SAM 3.1: no weights yet (facebook/sam3.1 access pending)" in detail and "(no SAM)" in detail
    assert "unreachable" not in detail and "/health" not in asked


def test_warm_up_never_loads_sam(monkeypatch: pytest.MonkeyPatch) -> None:
    """SAM 3.1 and Glimmer do not fit on the 5090 together: the warm-up loads Glimmer only, in every setting."""
    monkeypatch.setattr(backend, "_last_load_s", None)
    asked: list[str] = []

    def h(r: httpx.Request) -> httpx.Response:
        asked.append(r.url.path)
        return httpx.Response(200, json={"done": True, "load_duration": 14_200_000_000})

    ok, detail = asyncio.run(backend.warm_up(load_config(), transport=httpx.MockTransport(h)))
    assert ok and "SAM" not in detail and asked == ["/api/generate"]


def test_local_sam_is_off_beside_glimmer_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FIXIT_LOCAL_SAM")
    asked: list[str] = []

    def h(r: httpx.Request) -> httpx.Response:
        asked.append(r.url.path)
        return httpx.Response(200, json={"models": [{"name": "muse-glimmer:30b"}]})

    cfg = load_config()
    ok, detail = asyncio.run(backend.health(cfg, transport=httpx.MockTransport(h)))
    assert ok and "Local SAM 3.1: on demand, not used beside Glimmer" in detail and "(no SAM)" in detail
    assert "/health" not in asked
    from calls import make_client

    client = make_client("live", local=True, private=tmp_path)  # a live client needs a ledger
    assert cast(backend.LocalBackend, client.backend).sam_url == ""
    assert asyncio.run(w.provider(client)) is w.track_witness
    monkeypatch.setenv("FIXIT_LOCAL_SAM", "1")
    client = make_client("live", local=True, private=tmp_path)
    assert cast(backend.LocalBackend, client.backend).sam_url == cfg.local_sam_url


def test_preflight_detail_reads_as_plain_sentences(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a judge sees on /preflight: product names, sentence case, no ids or jargon (polish finding 24)."""
    monkeypatch.delenv("FIXIT_LOCAL_SAM")
    monkeypatch.setattr(backend, "_last_load_s", 14.2)
    ok, detail = asyncio.run(backend.health(load_config(), transport=_ollama(True, False)))
    assert detail.split(backend.DETAIL_SEP) == [
        "Muse Glimmer 30B (local): installed, loads on first use (~14 s)",
        "Local SAM 3.1: on demand, not used beside Glimmer",
        "Second check: Glimmer finds the part and motion tracking follows its place (no SAM)",
    ]
    assert not any(jargon in detail for jargon in ("muse-glimmer:30b", "optical-flow", "witness", "glimmer:"))


def test_the_check_page_reads_the_words_health_says() -> None:
    """deploy/preflight.html picks the local line's plain text by these phrases in the server's detail; if health()
    rewords them, the page would call a loaded model 'loads on first use' or a missing one 'doesn't answer'."""
    html = (Path(__file__).resolve().parents[2] / "deploy" / "preflight.html").read_text()
    for installed, loaded, phrase in [(True, True, "loaded and ready"), (False, False, "not installed")]:
        _, detail = asyncio.run(backend.health(load_config(), transport=_ollama(installed, loaded)))
        assert phrase in detail and f'd.includes("{phrase}")' in html


def test_with_glimmer_down_the_line_gives_only_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIXIT_LOCAL_SAM")
    for transport, want in [(_ollama(False, False), "Muse Glimmer 30B (local): not installed"),
                            (httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))),
                             "Muse Glimmer 30B (local): can't be reached (ConnectError)")]:
        assert asyncio.run(backend.health(load_config(), transport=transport)) == (False, want)
