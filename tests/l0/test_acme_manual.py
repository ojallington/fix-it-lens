"""The fake Acme WM-0000 manual: generated, never committed (the guard refuses every PDF)."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from tools import guard, make_acme_manual


def test_generates_six_pages_with_matching_text(tmp_path: Path) -> None:
    pdf = make_acme_manual.write(tmp_path / "acme-wm-0000.pdf")
    reader = PdfReader(pdf)
    assert len(reader.pages) == 6 == len(make_acme_manual.page_text_sha256())
    text = " ".join((reader.pages[3].extract_text() or "").split())
    assert "Do not remove the filter while the drum still holds water." in text


def test_guard_refuses_it(tmp_path: Path) -> None:
    pdf = make_acme_manual.write(tmp_path / "acme-wm-0000.pdf")
    hits = guard.scan(guard.given_files([str(pdf)]), guard.load_secrets(None))
    assert hits
