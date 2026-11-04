"""The fillable consent PDFs are built from the approved Markdown forms (eval/consent/pdf/build.py)."""

import importlib.util
import sys
from pathlib import Path

import pytest

BUILD = Path(__file__).resolve().parents[2] / "eval" / "consent" / "pdf" / "build.py"
spec = importlib.util.spec_from_file_location("consent_pdf", BUILD)
assert spec and spec.loader
pdf = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pdf  # dataclasses look their module up by name
spec.loader.exec_module(pdf)


@pytest.mark.parametrize("name", pdf.FORMS)
def test_both_halves_give_the_same_fields(name: str) -> None:
    form = pdf.parse(name)
    md = (BUILD.parents[1] / f"{name}.md").read_text().split("## Sources")[0]
    boxes = sum(len(c.options) for c in form.choices)
    assert boxes * 2 == md.count("☐")  # every box, in both languages, becomes one checkbox
    assert {b[0] for b in form.blanks} >= {"name", "date", "signature"}
    assert all(form.paragraphs[lang] for lang in pdf.LANGS) and all(form.declaration)
    assert not any("☐" in p or "____" in p for lang in pdf.LANGS for p in form.paragraphs[lang])


def test_the_video_form_asks_face_voice_and_credit_with_a_written_name() -> None:
    form = pdf.parse("video-consent")
    assert [c.key for c in form.choices] == ["voice", "face", "credit"]
    credit = form.choices[2]
    assert credit.text_option == 1 and credit.options[1][1] == "Credit me as:"
    assert "appliance" in {b[0] for b in form.blanks}


def test_a_halves_mismatch_is_refused(tmp_path: Path) -> None:
    src = (BUILD.parents[1] / "video-consent.md").read_text()
    (tmp_path / "video-consent.md").write_text(src.replace("☐ Mijn gezicht mag ook in beeld", "", 1))
    with pytest.raises(ValueError, match="options"):
        pdf.parse("video-consent", tmp_path)
