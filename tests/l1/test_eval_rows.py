"""The server's eval rows: each prints exactly the claims rows.toml lists (n=0 before the clips), and scores labelled
media through the production paths (here with fake calls). Media missing or with another hash is skipped, never
scored."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import calls
from app import eval as l1eval
from eval.rows import load_rows
from tests.l1.helpers import jpeg, sam_result

OWN_ROWS = [r for r in load_rows() if r.owner == "l1"]


@pytest.mark.parametrize("name", sorted(l1eval.ROWS))
def test_row_prints_its_claims_unmeasured(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(l1eval, "LABELS", tmp_path / "labels")
    row = next(r for r in OWN_ROWS if r.command == f"python -m app.eval {name}")
    out = asyncio.run(l1eval.ROWS[name]())
    assert sorted(c["id"] for c in out) == sorted(row.claims)
    assert all(c["n"] == 0 and c["value"] is None for c in out)
    assert all(Path(l1eval.config.ROOT / f).is_dir() and any((l1eval.config.ROOT / f).iterdir()) for f in row.fixtures)


def test_wilson_and_word_errors() -> None:
    assert l1eval.wilson(40, 40) == [91.2, 100.0]
    assert l1eval.wilson(0, 40) == [0.0, 8.8]
    assert l1eval.word_errors(["clean", "the", "filter"], ["clean", "a", "filter", "now"]) == 2


def media(tmp: Path, monkeypatch: pytest.MonkeyPatch, row: str, ext: str, items: list[tuple[dict[str, Any], bytes]],
          bad: int = 0) -> None:
    monkeypatch.setattr(l1eval, "LABELS", tmp / "labels")
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp / "private"))
    (tmp / "labels").mkdir(exist_ok=True)
    d = tmp / "private" / "clips-raw" / row
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (label, data) in enumerate(items):
        (d / f"{label['id']}{ext}").write_bytes(data)
        lines.append({**label, "sha256": hashlib.sha256(data if i >= bad else b"other").hexdigest()})
    (tmp / "labels" / f"{row}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


class Fake:
    """SAM: a plug box over the socket for frames whose bytes say 'in'; a transcript per request."""

    def __init__(self) -> None:
        self.heard: list[list[str]] = []

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        plugged = frame.endswith(b"in")
        if prompt == "socket":
            return sam_result([("s", None, (600, 600, 700, 700), 0.9)])
        return sam_result([("p", None, (610, 610, 690, 690) if plugged else (100, 100, 200, 200), 0.9)])

    async def transcribe(self, audio: bytes, bias: Any, keywords: list[str], **kw: Any) -> calls.Transcript:
        self.heard.append(keywords)
        return calls.Transcript("clean the filter" if keywords else "clean a filter", {}, 900.0, 0.0, "0" * 64)


def test_plugout_counts_out_and_false_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = [({"id": f"o{i}", "truth": "out"}, jpeg() + b"out") for i in range(3)]
    ins = [({"id": f"i{i}", "truth": "in"}, jpeg() + b"in") for i in range(2)]
    media(tmp_path, monkeypatch, "plug", ".jpg", out + ins, bad=1)
    monkeypatch.setattr(l1eval, "client_for", lambda row: Fake())
    got = {c["id"]: c for c in asyncio.run(l1eval.plugout())}
    assert (got["l1.plugout.unplugged"]["value"], got["l1.plugout.unplugged"]["n"]) == (100.0, 2)  # one skipped
    assert (got["l1.plugout.false_unplugged"]["value"], got["l1.plugout.false_unplugged"]["n"]) == (0.0, 2)
    assert "1 labelled item(s) skipped" in got["l1.plugout.unplugged"]["note"]


def test_voice_wer_with_and_without_keywords(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media(tmp_path, monkeypatch, "voice", ".wav",
          [({"id": "v1", "lang": "en", "reference": "Clean the filter.", "card": "bosch/SMV4HAX40N"}, b"RIFF")])
    fake = Fake()
    monkeypatch.setattr(l1eval, "client_for", lambda row: fake)
    got = {c["id"]: c for c in asyncio.run(l1eval.voice())}
    assert got["l1.voice.wer.keywords"]["value"] == 0.0 and got["l1.voice.wer.plain"]["value"] == 33.3
    assert got["l1.voice.wer.plain"]["n"] == 3 and fake.heard[0] and fake.heard[1] == []


def test_gastap_needs_iou_half(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media(tmp_path, monkeypatch, "gastap", ".jpg",
          [({"id": "b1", "consent": "neighbour", "box": [100, 100, 300, 300]}, jpeg()),
           ({"id": "b2", "source_url": "https://example.org/b2", "box": [100, 100, 300, 300]}, jpeg(shade=40))])
    boxes = iter([(110, 110, 300, 300), (250, 250, 500, 500)])

    async def fake_locate(models: Any, part: Any, frame: Any, lang: str) -> Any:
        return l1eval.locate.Located(True, next(boxes), False, "", 1.0)

    monkeypatch.setattr(l1eval, "client_for", lambda row: Fake())
    monkeypatch.setattr(l1eval.locate, "locate", fake_locate)
    [c] = asyncio.run(l1eval.gastap())
    assert (c["value"], c["n"]) == (50.0, 2)
