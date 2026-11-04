"""read_plate on recorded responses: no invented field, O/0 marked amber, a code failing the brand pattern asks for
a closer photo. The photos are generated grey squares; the replies are what Spark returned in the
probe's failure modes, written as fixtures."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import calls
from calls.testing import responses_events, write_respond_fixture
from ingest import plate as pl


def _photo(shade: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (shade, shade, shade)).save(buf, "JPEG")
    return buf.getvalue()


async def _read(tmp_path: Path, reply: dict[str, Any], shade: int = 128, **kw: Any) -> pl.PlateCard:
    client = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    photo = _photo(shade)
    body = client.build_respond_body("plate", pl.PLATE_PROMPT, images=[photo], format=pl.PLATE_FORMAT)
    write_respond_fixture(tmp_path, body, responses_events(text=json.dumps(reply)))
    return await pl.read_plate(photo, client=client, **kw)


def _reply(brand: str | None, model: str | None, product: str | None = None, visible: bool = True,
           unreadable: list[str] | None = None) -> dict[str, Any]:
    return {"plate_visible": visible, "brand": brand, "model_code": model, "product_code": product,
            "unreadable_fields": unreadable or []}


async def test_clean_read(tmp_path: Path) -> None:
    card = await _read(tmp_path, _reply("BOSCH", "SMV4HAX4ON/17"))
    # the letter O between digits... still fits the pattern, so it is amber rather than silently fixed
    assert card.brand == "bosch" and card.closer_photo is False


async def test_o_zero_is_amber(tmp_path: Path) -> None:
    card = await _read(tmp_path, _reply("Bosch", "SMV4HAX40N/17"))
    assert card.model == "SMV4HAX40N/17"
    assert card.confirm_needed and card.amber_chars == [8]
    model_field = next(f for f in card.fields if f.name == "model")
    assert model_field.uncertain[0].options == ["0", "O"]
    assert card.ws_fields()[1]["uncertain"] == [{"index": 8, "options": ["0", "O"]}]


async def test_a_known_card_settles_it(tmp_path: Path) -> None:
    card = await _read(tmp_path, _reply("Bosch", "SMV4HAX4ON/17"), known_models=["SMV4HAX40N/17"])
    assert card.model == "SMV4HAX40N/17" and not card.confirm_needed and card.reason == "ok"


async def test_pattern_fixes_a_digit_letter_swap(tmp_path: Path) -> None:
    """A Bosch E-Nr starts with letters: '5' read for 'S' has one fitting spelling, so it is fixed, not amber."""
    card = await _read(tmp_path, _reply("BOSCH", "5MV4HAX40N/17"))
    assert card.model == "SMV4HAX40N/17" and card.amber_chars == [8]


def test_samsung_reading_that_fits_is_kept() -> None:
    card = pl.resolve("samsung", "WW90T534AAW/S2")
    assert card.model == "WW90T534AAW/S2" and card.amber_chars == [3]  # 90 or 9O


async def test_failing_brand_pattern_asks_closer_photo(tmp_path: Path) -> None:
    card = await _read(tmp_path, _reply("Samsung", "XQ-12"))
    assert card.closer_photo and card.model is None and card.reason == "pattern"
    assert [f.name for f in card.fields] == ["brand"]


async def test_no_invented_fields(tmp_path: Path) -> None:
    card = await _read(tmp_path, _reply(None, None, visible=True, unreadable=["model_code", "brand"]))
    assert card.fields == [] and card.model is None and card.brand is None
    assert card.closer_photo and card.reason == "unreadable"


async def test_only_returned_fields_appear(tmp_path: Path) -> None:
    card = await _read(tmp_path, _reply(None, "SMV4HAX40N/17"))
    assert [f.name for f in card.fields] == ["model"]  # no brand was returned, none is shown


async def test_no_plate(tmp_path: Path) -> None:
    card = await _read(tmp_path, _reply(None, None, visible=False))
    assert card.closer_photo and card.reason == "no_plate"


def test_miele_el2_spelling() -> None:
    """The probe's systematic misread: `El 2` read as `EI2`. l/I/1 is amber, never silently taken."""
    card = pl.resolve("miele", "Wa 75 T1/EI2")
    assert card.model is not None and card.confirm_needed
    assert {"I", "L", "1"} <= {o for f in card.fields for u in f.uncertain for o in u.options}


def test_too_many_ambers_is_a_closer_photo() -> None:
    card = pl.resolve("bosch", "SMS00O0O0E/13")
    assert card.closer_photo and card.reason == "too_uncertain"


def test_unknown_maker_is_taken_as_read() -> None:
    card = pl.resolve("intergas", "Kombi Kompakt HRE 28/24")
    assert card.model == "KOMBI KOMPAKT HRE 28/24" and not card.closer_photo


@pytest.mark.parametrize(("raw", "slug"), [("BOSCH", "bosch"), ("Robert Bosch", "bosch"), ("Samsung", "samsung"),
                                          ("Miele & Cie.", "miele"), ("", None), ("Intergas", "intergas")])
def test_brand_slug(raw: str, slug: str | None) -> None:
    assert pl.brand_slug(raw) == slug


def test_the_serial_is_never_asked_for() -> None:
    assert "serial" not in json.dumps(pl.PLATE_SCHEMA).lower()


@pytest.mark.parametrize("code, brand", [
    ("SMV4HAX40N", "bosch"), ("WAT28400NL", "bosch"), ("SN23HI60AE", "siemens"), ("WM14N296NL", "siemens"),
    ("WW90T534AAW/S2", "samsung"), ("WWD120WCS", "miele"), ("G7110SCI", "miele"), ("L7FEE865R", "aeg"),
    ("FSE63716P", "aeg"), ("12345", None), ("X", None),
])
def test_guess_brand(code: str, brand: str | None) -> None:
    assert pl.guess_brand(code) == brand
