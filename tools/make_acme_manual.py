"""Write the dummy manual "Acme WM-0000": the only manual tests ever see.

An obviously fake 6-page washer manual with an interval, a filter step, a "do not open the casing" line and a
negation. The PDF is generated, never committed (*.pdf is ignored and the guard blocks PDFs):

    python tools/make_acme_manual.py [out.pdf]     # default tests/data/acme-wm-0000.pdf
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tests" / "data" / "acme-wm-0000.pdf"

PAGES: list[list[str]] = [
    [
        "ACME WM-0000 WASHING MACHINE",
        "User manual",
        "THIS IS A FAKE MANUAL FOR SOFTWARE TESTS.",
        "Acme is not a real company and the WM-0000 does not exist.",
    ],
    [
        "1 Safety",
        "Do not open the casing. Behind the back panel is mains power.",
        "Only a qualified technician may remove the back panel.",
        "Never clean the filter while the machine is running.",
    ],
    [
        "2 Care and maintenance",
        "Clean the debris filter every 3 months.",
        "Do not use solvents to clean the drum.",
        "Wipe the door seal with a dry cloth after each wash.",
    ],
    [
        "3 Cleaning the debris filter",
        "Unplug the machine from the socket before you start.",
        "Open the filter flap at the bottom right of the front panel.",
        "Do not remove the filter while the drum still holds water.",
        "Place a shallow tray under the filter and turn the filter knob anticlockwise.",
        "Rinse the filter under running water and remove any fluff.",
        "Screw the filter back in clockwise until the arrow marks line up.",
        "The whole job takes about 10 minutes.",
    ],
    [
        "4 Fault codes",
        "E21: the water does not drain. Clean the debris filter.",
        "E99: do not try to repair this yourself. Call a technician.",
    ],
    [
        "5 Technical data",
        "Rated voltage 230 V, 50 Hz. Fake data for tests only.",
        "Acme WM-0000 user manual, document AC-0000-EN, edition 0.",
    ],
]


def page_text(n: int) -> str:
    """The canonical text of page n (1-based): its lines joined by newlines."""
    return "\n".join(PAGES[n - 1])


def page_text_sha256() -> list[str]:
    return [hashlib.sha256(page_text(i + 1).encode()).hexdigest() for i in range(len(PAGES))]


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf() -> bytes:
    objects: list[bytes] = []
    n_pages = len(PAGES)
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, lines in enumerate(PAGES):
        ops = ["BT", "/F1 12 Tf", "16 TL", "72 760 Td"]
        for j, line in enumerate(lines):
            ops.append(f"({_escape(line)}) Tj" if j == 0 else f"T* ({_escape(line)}) Tj")
        ops += ["ET", "BT", "/F1 9 Tf", "72 40 Td", f"(Page {i + 1}) Tj", "ET"]
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for k, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % k + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info << /Title (Acme WM-0000 user manual - FAKE) >> >>\n" % (
        len(objects) + 1
    )
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def write(out: Path = DEFAULT_OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_pdf())
    return out


if __name__ == "__main__":
    print(write(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT))
