"""A fault row's symbol, found by code: the small image on the row's line, as fractions of the page from the top left;
a big picture, a row without an indicator or a page without images stays null. The PDFs are made here, by hand."""

from __future__ import annotations

from pathlib import Path

from ingest import symbols


def _pdf(path: Path, lines: list[tuple[float, str]], images: list[tuple[float, float, float]]) -> Path:
    """One A4 page (595 x 842 pt): text lines at (y, text) from x=100, images at (x, y, size)."""
    text = "".join(f"BT /F1 10 Tf 100 {y} Td ({t}) Tj ET\n" for y, t in lines)
    draw = "".join(f"q {s} 0 0 {s} {x} {y} cm /Im1 Do Q\n" for x, y, s in images)
    stream = (text + draw).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 "
        b"/Length 1 >>\nstream\n\x80\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(out)
    return path


def _row(rid: str, codes: list[str], indicator: bool = True) -> dict[str, object]:
    return {"id": rid, "codes": codes, "indicator": {"en": "The light flashes"} if indicator else None, "page": 1,
            "quote_ref": f"q_{rid}"}


def test_the_icon_on_the_rows_line_is_its_symbol(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "m.pdf", [(700, "E:21 The light flashes."), (600, "E99 Call a technician.")],
               [(80, 698, 12), (80, 400, 12), (300, 100, 300)])
    got = symbols.locate(pdf, [_row("e21", ["E:21"]), _row("e99", ["E99"]), _row("e2", ["E2"]),
                               _row("e21b", ["E21"], indicator=False)], {})
    # e99 has no icon on its line, E2 is printed nowhere as a word (never inside E:21), e21b has no indicator
    assert set(got) == {"e21"}
    x0, y0, x1, y1 = got["e21"]["bbox"]
    assert got["e21"]["page"] == 1
    assert (round(x0 * 595), round(x1 * 595)) == (80, 92)
    assert (round(y0 * 842), round(y1 * 842)) == (842 - 710, 842 - 698)  # top-left origin


def test_a_row_anchored_by_its_quote_and_a_photo_is_never_a_symbol(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "m.pdf", [(500, "Door light on: close the door.")], [(80, 420, 200)])
    assert symbols.locate(pdf, [_row("door", [])], {"q_door": "Door light on: close the door."}) == {}
    pdf = _pdf(tmp_path / "n.pdf", [(500, "Door light on: close the door.")], [(80, 498, 14)])
    assert set(symbols.locate(pdf, [_row("door", [])], {"q_door": "Door light on: close the door."})) == {"door"}
