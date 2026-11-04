"""The guard rejects a planted PDF, key, serial and -contributor ID (and a fixture request body)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import guard

FIELDS = """# Type plates
| Item | Brand | Model / type (E-Nr, model code) | Product code | Serial | Anything else |
|---|---|---|---|---|---|
| washer | Acme | WM-0000 | ACM 1234/56 | ZZ9PLURALZ42 | 230 V |
| boiler | | | | | |
"""


def _private(tmp_path: Path) -> guard.Secrets:
    (tmp_path / "plates").mkdir()
    (tmp_path / "plates" / "FIELDS.md").write_text(FIELDS)
    (tmp_path / "identifiers.txt").write_text("# comment\nTestlaan\n9999 ZZ\n")
    s = guard.load_secrets(tmp_path)
    return guard.Secrets(identifiers=s.identifiers, plate_values=s.plate_values, key=None)


def test_rejects_planted_pdf() -> None:
    hits = guard.scan([("docs/manual.bin", b"%PDF-1.4\n...")], guard.Secrets())
    assert hits and "PDF" in hits[0].reason
    assert guard.scan([("x/y.pdf", b"not really")], guard.Secrets())


def test_rejects_planted_key() -> None:
    fake = "LLM_" + "1234567890" + "_" + "abcdefABCDEF0123456789xyz"
    hits = guard.scan([("app/config.py", f'KEY = "{fake}"\n'.encode())], guard.Secrets())
    assert [h.reason for h in hits] == ["a key-shaped string"]
    assert fake not in str(hits[0])


def test_rejects_planted_serial_and_identifier(tmp_path: Path) -> None:
    secrets = _private(tmp_path)
    assert "ZZ9PLURALZ42" in secrets.plate_values and "ACM 1234/56" in secrets.plate_values
    assert "WM-0000" not in secrets.plate_values  # model codes are public and name the cards
    hits = guard.scan([("cards/acme/wm-0000.yaml", b"serial: zz9pluralz42\n")], secrets)
    assert [h.reason for h in hits] == ["matches a serial or product code from FIELDS.md"]
    assert "zz9" not in str(hits[0]).lower()
    hits = guard.scan([("docs/note.md", b"we live on the Testlaan, 9999ZZ\n")], secrets)
    assert len(hits) == 2 and all(h.reason == "matches identifiers.txt" for h in hits)
    assert not guard.scan([("cards/acme/wm-0000.yaml", b"model: WM-0000\n")], secrets)


def test_rejects_planted_contributor_id() -> None:
    model_id = "muse-spark-1.3-" + "contributor"
    hits = guard.scan([("config/models.toml", f'guide = "{model_id}"\n'.encode())], guard.Secrets())
    assert [h.reason for h in hits] == ["a -contributor model ID"]
    assert not guard.scan([("README.md", b"a `-contributor` ID is refused at load\n")], guard.Secrets())


def test_rejects_fixture_with_request_body() -> None:
    body = {"request_sha256": "ab", "request": {"input": [{"role": "user"}], "instructions": "x"}}
    hits = guard.scan([("fixtures/l1/ab.jsonl", json.dumps(body).encode())], guard.Secrets())
    assert hits and "request body" in hits[0].reason
    media = {"output": [{"text": "data:image/jpeg;base64,/9j/4AAQ"}]}
    assert guard.scan([("fixtures/l1/cd.jsonl", json.dumps(media).encode())], guard.Secrets())
    ok = {"request_sha256": "ab", "events": [{"t_ms": 3, "type": "response.output_text.delta", "delta": "ok"}]}
    assert not guard.scan([("fixtures/l1/ab.jsonl", json.dumps(ok).encode())], guard.Secrets())


def test_private_words_come_from_the_private_folder_and_fail_like_any_note(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "internal-words.txt").write_text("# a comment\n\nplanted-host-\n\\bPlantedname\\b\n")
    secrets = guard.load_secrets(private)
    for line, hit in ((b"# planted-host-one\n", True), (b"# Plantedname says\n", True), (b"# a plain note\n", False)):
        assert bool(guard.scan([("calls/x.py", line)], secrets)) is hit, line
    assert not guard.scan([("fixtures/l1/ab.jsonl", b'{"delta": "planted-host-"}\n')], secrets)
    assert guard.scan([("fixtures/l1/x/README.md", b"on planted-host-one\n")], secrets)
    assert "planted" not in str(guard.scan([("calls/x.py", b"# planted-host-one\n")], secrets)[0])  # never printed
    assert not guard.scan([("calls/x.py", b"# planted-host-one\n")], guard.load_secrets(tmp_path))  # no list, no words


def test_rejects_internal_build_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Planted from pieces so this file is not a hit itself.
    notes = ["owned by " + "L" + "3", "ask fil" + "-example", "(A1" + "64)", "closes with the Th" + "u 29 clips",
             "on lane" + "/agent", "Bri" + "ef L" + "1 §6", "binds to 100." + "96.12.7"]
    for note in notes:
        assert guard.scan([("calls/x.py", f"# {note}\n".encode())], guard.Secrets()), note
    assert not guard.scan([("calls/x.py", b"TAILNET = '100.64.0.0/10'  # 100.64.0.1\n")], guard.Secrets())
    # Fixtures hold recorded model text, binaries are not text, and plain product words pass.
    assert not guard.scan([("fixtures/l1/ab.jsonl", b'{"delta": "L' + b'1"}\n')], guard.Secrets())
    # but a fixture folder's README is read by people, so it is scanned.
    assert guard.scan([("fixtures/l1/x/README.md", b"from the Th" + b"u 29 clips\n")], guard.Secrets())
    assert guard.scan([("eval/fixtures/notes.txt", b"probe on lane" + b"/agent\n")], guard.Secrets())
    assert not guard.scan([("demo/f.jpg", b"\xff\xd8\0 L" + b"1 ")], guard.Secrets())
    # A directory still being scrubbed is not failed on, except under --notes.
    monkeypatch.setattr(guard, "INTERNAL_PENDING", ("vendor/",))  # the real list is empty
    pending = [("vendor/x.py", b"# ask fil" + b"-example\n")]
    assert not guard.scan(pending, guard.Secrets())
    assert guard.scan(pending, guard.Secrets(notes_everywhere=True))
    fine = ["M552 520 L704 520 Z", "facebookresearch/sam3", "a 1.5 L tank", "the eval's G0 rows", "LAN only"]
    assert not guard.scan([("calls/x.py", "\n".join(fine).encode())], guard.Secrets())


def test_repo_is_clean() -> None:
    root = Path(__file__).resolve().parents[2]
    skip = {".git", ".venv", "node_modules", "dist", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
    files = [
        (str(p.relative_to(root)), p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and not skip & set(p.parts) and p.suffix != ".pdf"
    ]
    assert [str(h) for h in guard.scan(files, guard.Secrets())] == []
