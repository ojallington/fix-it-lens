"""local/sam_server: the hosted sam-3.1 request in, the hosted events out, read by calls/ unchanged. A fake model
stands in for SAM 3.1 (no torch, no weights, no GPU)."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import httpx
from PIL import Image

from calls.backends.local import LocalBackend
from calls.client import Client
from calls.config import ROOT, load_config
from calls.ledger import Ledger
from local.sam_server.encode import Found, Prompt, parse_prompt
from local.sam_server.segmenter import crop_found
from local.sam_server.server import create_app


class FakeSam:
    def __init__(self) -> None:
        self.prompts: list[Prompt] = []

    def segment(self, media: Path, prompt: Prompt, size: tuple[int, int]) -> list[list[Found]]:
        self.prompts.append(prompt)
        assert sorted(p.name for p in media.iterdir()) == ["00000.jpg"]
        full = [[int(100 <= x <= 199 and 150 <= y <= 229) for x in range(size[0])] for y in range(size[1])]
        f = crop_found(1, 0.91, full)
        assert f is not None
        return [[f]]


def jpeg(w: int = 640, h: int = 480) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 90, 90)).save(buf, "JPEG")
    return buf.getvalue()


def client(tmp_path: Path, fake: FakeSam) -> Client:
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl7 = 2.0\n")
    cfg = load_config().local()
    backend = LocalBackend(cfg.local_base_url, cfg.local_sam_url,
                           transport=httpx.ASGITransport(app=create_app(fake)))
    return Client("live", cfg, backend, ledger=Ledger(private / "ledger.jsonl", private / "budgets.toml"),  # type: ignore[arg-type]
                  fixtures_root=tmp_path, private=private)


async def test_concept_segments_through_calls_unchanged(tmp_path: Path) -> None:
    fake = FakeSam()
    c = client(tmp_path, fake)
    r = await c.sam_image(jpeg(), "lint filter", lane="l7")
    (obj,) = r.objects
    assert obj.box_px == (100, 150, 200, 230) and obj.conf == 0.91 and obj.mask_svg  # parser: right, bottom are edges
    assert fake.prompts == [Prompt("lint filter", None)]
    assert r.usd == 0.0
    line = (tmp_path / "private" / "ledger.jsonl").read_text()
    assert '"mode": "local"' in line and '"response_model": "facebook/sam3.1"' in line


async def test_box_prompt_reaches_the_model_in_pixels(tmp_path: Path) -> None:
    fake = FakeSam()
    await client(tmp_path, fake).sam_image(jpeg(), (156, 312, 312, 479), lane="l7")
    assert fake.prompts[0].box is not None and fake.prompts[0].text is None
    assert parse_prompt("<0f>1<|box;x1=10;y1=20;x2=30;y2=40;w=640;h=480|>").box == (10, 20, 30, 40)


async def test_health_reports_weights() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(None)), base_url="http://x") as h:
        body = (await h.get("/health")).json()
    assert body["model"] == "facebook/sam3.1" and body["loaded"] is False


def test_no_weights_or_tokens_are_tracked() -> None:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert not [p for p in tracked.splitlines() if p.endswith((".pt", ".safetensors", ".ckpt", ".token", ".gguf"))]


def test_fetch_verifies_the_checkpoint_sha(tmp_path: Path) -> None:
    import hashlib
    import json

    from local.sam_server import fetch

    ckpt = tmp_path / "sam3.1_multiplex.pt"
    ckpt.write_bytes(b"not really weights")
    good = hashlib.sha256(ckpt.read_bytes()).hexdigest()
    assert fetch.verify(ckpt, "0" * 64) == 4 and not (tmp_path / "verified.json").exists()
    assert fetch.verify(ckpt, f'"{good}"') == 0
    assert json.loads((tmp_path / "verified.json").read_text())["sha256"] == good


async def test_idle_model_is_unloaded() -> None:
    from local.sam_server import server

    app = create_app(idle_s=0.01)
    state = app.state.sam
    state["seg"] = FakeSam()
    state["last"] -= 1.0
    await app.state.reap_once()
    assert state["seg"] is None
    keep = create_app(FakeSam(), idle_s=0.01)  # a model handed in stays: the reaper never starts for it
    assert keep.state.sam["seg"] is not None and server.IDLE_S >= 0


def test_gate_status_words() -> None:
    from local.sam_server.gate import status

    assert status(200) == ("granted", 0) and status(302) == ("granted", 0)
    assert status(403) == ("pending", 3) and status(401) == ("pending", 3)
    assert status(500)[1] == 1


def test_unknown_kwargs_are_dropped() -> None:
    from local.sam_server.segmenter import _drop_unknown_kwargs

    class Model:
        def init_state(self, resource_path: str, offload_video_to_cpu: bool = False) -> str:
            return resource_path

    m = Model()
    _drop_unknown_kwargs(m, "init_state")
    assert m.init_state(resource_path="x", offload_video_to_cpu=False, offload_state_to_cpu=False) == "x"


async def test_warm_loads_the_model() -> None:
    app = create_app(FakeSam())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sam") as h:
        assert (await h.post("/warm")).json()["loaded"] is True
        assert (await h.get("/health")).json()["loaded"] is True
