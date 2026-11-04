"""A manual's pages: extracted once with pypdf, OCR'd with tesseract where there is no text layer, cached as JSON
next to the PDF in FIXIT_PRIVATE/manuals/. Page text never leaves the private folder; a card holds only each page's
hash (`page_hash`).

    FIXIT_PRIVATE/manuals/<brand>/<model>.pdf          the maker's PDF
    FIXIT_PRIVATE/manuals/<brand>/<model>.pages.json   the cache (pdf sha256, then one entry per page)
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader

import calls

OCR_BELOW_CHARS = 30
OCR_DPI = 300
OCR_LANGS = "nld+eng"


@dataclass(frozen=True)
class Page:
    number: int  # 1-based PDF index; cards cite this
    label: str  # the printed page label, when the PDF has one
    text: str  # pypdf's extraction (or tesseract's, when ocr)
    ocr: bool = False


def page_hash(text: str) -> str:
    """sha256 of a page's text with whitespace collapsed, so the same PDF hashes alike on every machine."""
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ocr(pdf: Path, index: int, langs: str) -> str:
    if shutil.which("tesseract") is None:
        return ""
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    doc = pdfium.PdfDocument(str(pdf))
    try:
        image = doc[index].render(scale=OCR_DPI / 72).to_pil()
    finally:
        doc.close()
    available = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout.split()
    use = "+".join(lang for lang in langs.split("+") if lang in available) or "eng"
    with tempfile.TemporaryDirectory() as d:
        png = Path(d) / "page.png"
        image.save(png)
        out = subprocess.run(["tesseract", str(png), "-", "-l", use], capture_output=True, text=True, timeout=120)
    return out.stdout


def extract(pdf: Path, *, ocr_langs: str = OCR_LANGS) -> list[Page]:
    reader = PdfReader(pdf)
    labels = list(reader.page_labels) if reader.page_labels else []
    pages: list[Page] = []
    for i, p in enumerate(reader.pages):
        text = p.extract_text() or ""
        ocr = False
        if len(text.strip()) < OCR_BELOW_CHARS:
            ocr_text = _ocr(pdf, i, ocr_langs)
            if len(ocr_text.strip()) > len(text.strip()):
                text, ocr = ocr_text, True
        pages.append(Page(i + 1, labels[i] if i < len(labels) else str(i + 1), text, ocr))
    return pages


def load(pdf: Path) -> list[Page]:
    """The pages of a PDF, from the cache beside it when the PDF has not changed."""
    cache = pdf.with_suffix(".pages.json")
    sha = file_sha256(pdf)
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get("pdf_sha256") == sha:
            return [Page(**p) for p in data["pages"]]
    pages = extract(pdf)
    if pdf.parent.is_relative_to(manuals_dir() or Path("/nonexistent")):
        cache.write_text(json.dumps({"pdf_sha256": sha, "pages": [asdict(p) for p in pages]}, ensure_ascii=False))
    return pages


def manuals_dir() -> Path | None:
    priv = calls.private_dir()
    return priv / "manuals" if priv is not None else None


def home_dir(root: Path, household: str) -> Path:
    """A household's own folder under a private root (cards or manuals): `homes/<household>`."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", household):
        raise ValueError("a household id is letters, digits, _ and - only")
    return root / "homes" / household


def manual_path(brand: str, model: str, household: str | None = None) -> Path:
    """The manual's PDF: the household's own copy when it has one (an upload, or the manual its private card cites),
    else the shared one. With `household`, a path that does not exist yet is the household's."""
    root = manuals_dir()
    if root is None:
        raise FileNotFoundError("FIXIT_PRIVATE is not set and the default private folder does not exist")
    shared = root / brand / f"{model}.pdf"
    if household is None:
        return shared
    return home_dir(root, household) / brand / f"{model}.pdf"


def manual_pages(brand: str, model: str, household: str | None = None) -> list[Page]:
    """Every page of this appliance's manual, for the guide's prefix: the household's copy first, then the shared
    one. Raises FileNotFoundError if absent."""
    if household is not None and (own := manual_path(brand, model, household)).exists():
        return load(own)
    return load(manual_path(brand, model))
