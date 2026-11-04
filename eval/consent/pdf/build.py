"""The consent forms as fillable PDFs: one per form, Dutch then English, A4, signed on a phone or in Acrobat.

    uv run --with reportlab,pypdf python eval/consent/pdf/build.py          # -> eval/consent/pdf/<form>.pdf
    uv run --with reportlab,pypdf python eval/consent/pdf/build.py --check  # also list the fields, fill a test copy

The words come from the approved Markdown form (eval/consent/<form>.md), so a PDF never says anything the form does
not. Each language's paragraphs are printed as written; the tick boxes, the declaration and the lines to fill in are
gathered once at the end, both languages side by side, as real form fields: a checkbox for every box (and a text field
beside "Credit me as"), text fields for name, date and the rest, and an empty signature field. Acrobat signs the
signature field; iPhone Markup and Android place a drawn signature in the box drawn around it.

The PDFs are gitignored: a blank form is rebuilt from the Markdown, and a signed one is a household's data and lives
in FIXIT_PRIVATE/consent/ only.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FORMS = ("pilot-consent", "video-consent", "feedback")
LANGS = ("Nederlands", "English")
FOOTER = ("Fix-It Lens · CraftyCast", "pilot@craftycast.com")
BLANK = re.compile(r"([^:_]+?):\s*_{3,}")
KEY = re.compile(r"\s*<!--\s*([a-z_]+)\s*-->")  # a field's name in the record, not shown
OPEN = re.compile(r"\n_{3,}\s*$")  # a question answered in writing: its paragraph ends in a line of underscores


@dataclass
class Choice:
    key: str
    label: tuple[str, str]  # (nl, en)
    lead: tuple[str, str]
    options: list[tuple[str, str]]
    text_option: int | None = None  # the option that takes a written answer ("Credit me as: ____")
    open: bool = False  # answered in writing, no boxes


@dataclass
class Form:
    name: str
    title: str
    meta: str
    paragraphs: dict[str, list[str]]  # language -> its paragraphs, as written
    choices: list[Choice] = field(default_factory=list)
    declaration: tuple[str, str] = ("", "")
    blanks: list[tuple[str, str, str]] = field(default_factory=list)  # (key, nl label, en label)


def _key(en: str) -> str:
    return re.sub(r"[^a-z]+", "_", re.sub(r"\(.*?\)", "", en.lower())).strip("_")


def _choice(p: str) -> tuple[str, str, list[str], str | None]:
    key = KEY.search(p)
    p = KEY.sub("", p)
    m = re.match(r"\*\*(.+?)\*\*\s*(.*)", p, re.S)
    if not m:
        raise ValueError(f"a question without a bold label: {p[:40]!r}")
    lead, *opts = OPEN.sub("", m.group(2)).split("☐")
    return (m.group(1).rstrip(". "), " ".join(lead.split()), [" ".join(o.split()) for o in opts],
            key.group(1) if key else None)


def parse(name: str, root: Path = HERE.parent) -> Form:
    text = (root / f"{name}.md").read_text()
    lines = text.splitlines()
    form = Form(name, lines[0].lstrip("# ").strip(), lines[2].strip(), {})
    found: dict[str, tuple[list[tuple[str, str, list[str], str | None]], str, list[str]]] = {}
    for lang in LANGS:
        body = text.split(f"## {lang}\n", 1)[1].split("\n---", 1)[0]
        paras, choices, decl, blanks = [], [], "", []
        for p in (x.strip() for x in body.split("\n\n") if x.strip()):
            if OPEN.search(p):
                choices.append(_choice(p))
            elif "☐" in p:
                choices.append(_choice(p))
                if lead := choices[-1][1]:  # "In a session you talk to the app." stays in its place in the text
                    paras.append(f"**{choices[-1][0]}.** {lead}")
            elif BLANK.search(p):
                blanks += [b.strip() for b in BLANK.findall(p)]
            elif p.startswith("**"):
                paras.append(" ".join(p.split()))
            else:
                decl = " ".join(p.split())
        form.paragraphs[lang] = paras
        found[lang] = (choices, decl, blanks)
    (nl_c, nl_d, nl_b), (en_c, en_d, en_b) = found["Nederlands"], found["English"]
    if len(nl_c) != len(en_c) or len(nl_b) != len(en_b) or not (nl_d and en_d):
        raise ValueError(f"{name}: the Dutch and English halves differ in tick boxes, blanks or declaration")
    for (nl_l, nl_lead, nl_o, nl_k), (en_l, en_lead, en_o, en_k) in zip(nl_c, en_c, strict=True):
        if len(nl_o) != len(en_o) or nl_k != en_k:
            raise ValueError(f"{name}: '{en_l}' has {len(nl_o)} Dutch and {len(en_o)} English options, "
                             f"keys {nl_k} and {en_k}")
        written = [i for i, o in enumerate(en_o) if BLANK.search(o)]
        opts = [(BLANK.sub(r"\1:", n).strip(), BLANK.sub(r"\1:", e).strip()) for n, e in zip(nl_o, en_o, strict=True)]
        form.choices.append(Choice(en_k or _key(en_l.split()[-1]), (nl_l, en_l), (nl_lead, en_lead), opts,
                                   written[0] if written else None, open=not en_o))
    form.declaration = (nl_d, en_d)
    form.blanks = [(_key(e), n, e) for n, e in zip(nl_b, en_b, strict=True)]
    return form


def _md(s: str) -> str:
    """**bold** to the markup reportlab's Paragraph reads; & and < escaped first."""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)


def render(form: Form, out: Path) -> list[tuple[int, list[float]]]:
    """Writes the PDF with its text and checkbox fields; returns where the signature boxes are (page, rect)."""
    from reportlab.lib import colors  # type: ignore[import-untyped,import-not-found,unused-ignore]
    from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped,import-not-found,unused-ignore]
    from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped,import-not-found,unused-ignore]
    from reportlab.lib.units import mm  # type: ignore[import-untyped,import-not-found,unused-ignore]
    from reportlab.platypus import (  # type: ignore[import-untyped,import-not-found,unused-ignore]
        CondPageBreak,
        Flowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    ink, quiet, rule = colors.HexColor("#1a1a1a"), colors.HexColor("#5c5c5c"), colors.HexColor("#9a9a9a")
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13.2, textColor=ink, spaceAfter=7)
    en_style = ParagraphStyle("en", parent=body, textColor=quiet, spaceAfter=0)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11, textColor=quiet)
    title = ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=15, leading=19, spaceAfter=3)
    h2 = ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=8,
                        spaceAfter=6)
    label = ParagraphStyle("label", parent=body, fontName="Helvetica-Bold", spaceAfter=2)
    LABEL_W = 62 * mm  # noqa: N806  the label column beside each line to fill in
    sig_boxes: list[tuple[int, list[float]]] = []

    class Field(Flowable):  # type: ignore[misc]
        """One line of the fill-in block: a box or a written answer, with its Dutch and English words."""

        def __init__(self, kind: str, name: str, nl: str, en: str, tip: str) -> None:
            super().__init__()
            self.kind, self.name, self.tip = kind, name, tip
            self.text = Paragraph(f"{_md(nl)}<br/><font color='#5c5c5c'>{_md(en)}</font>", body)
            self.words = Paragraph(f"<b>{_md(nl)}</b><br/><font color='#5c5c5c'>{_md(en)}</font>", small)

        def wrap(self, aw: float, ah: float) -> tuple[float, float]:
            if self.kind == "check":
                _, h = self.text.wrap(aw - 9 * mm, ah)
                self.height = max(h - 7, 5 * mm) + 2.5 * mm
            elif self.kind == "sign":
                self.height = 26 * mm
            else:
                self.height = 12 * mm
            self.width = aw
            return aw, self.height

        def draw(self) -> None:
            c, form_ = self.canv, self.canv.acroForm
            if self.kind == "check":
                form_.checkbox(name=self.name, tooltip=self.tip, x=0, y=self.height - 4.1 * mm, size=3.8 * mm,
                               buttonStyle="check", borderColor=ink, borderWidth=0.8, fillColor=colors.white,
                               textColor=ink, forceBorder=True, relative=True)
                self.text.wrap(self.width - 9 * mm, self.height)
                self.text.drawOn(c, 8 * mm, self.height - self.text.height + 1)
                return
            self.words.wrap(LABEL_W - 3 * mm, self.height)
            top = self.height - 2 * mm  # the label's first line sits level with the field's writing line
            if self.kind == "sign":
                self.words.drawOn(c, 0, top - self.words.height)
                c.setStrokeColor(rule)
                c.setLineWidth(0.8)
                c.rect(LABEL_W, 2 * mm, 100 * mm, 22 * mm)
                x, y = c.absolutePosition(LABEL_W, 2 * mm)
                sig_boxes.append((c.getPageNumber() - 1, [x, y, x + 100 * mm, y + 22 * mm]))
                return
            self.words.drawOn(c, 0, 2 * mm + 7 * mm - self.words.height + 1)
            form_.textfield(name=self.name, tooltip=self.tip, x=LABEL_W, y=2 * mm, width=100 * mm, height=7 * mm,
                            fontName="Helvetica", fontSize=11, borderStyle="underlined", borderColor=rule,
                            borderWidth=0.8, fillColor=colors.white, textColor=ink, relative=True)

    def written(name: str, tip: str) -> Flowable:
        class Answer(Flowable):  # type: ignore[misc]
            def wrap(self, aw: float, ah: float) -> tuple[float, float]:
                self.width, self.height = aw, 9 * mm
                return aw, self.height

            def draw(self) -> None:
                self.canv.acroForm.textfield(name=name, tooltip=tip, x=8 * mm, y=1 * mm, width=87 * mm,
                                             height=7 * mm, fontName="Helvetica", fontSize=11,
                                             borderStyle="underlined", borderColor=rule, borderWidth=0.8,
                                             fillColor=colors.white, textColor=ink, relative=True)
        return Answer()

    def writing(name: str, tip: str) -> Flowable:
        class Lines(Flowable):  # type: ignore[misc]
            """A written answer: a box of a few lines, the text wrapping inside it."""

            def wrap(self, aw: float, ah: float) -> tuple[float, float]:
                self.width, self.height = aw, 19 * mm
                return aw, self.height

            def draw(self) -> None:
                self.canv.acroForm.textfield(name=name, tooltip=tip, x=0, y=1 * mm, width=self.width,
                                             height=17 * mm, fontName="Helvetica", fontSize=10,
                                             fieldFlags="multiline", borderStyle="solid", borderColor=rule,
                                             borderWidth=0.8, fillColor=colors.white, textColor=ink, relative=True)
        return Lines()

    def footer(c: Any, doc: Any) -> None:
        c.saveState()
        c.setFont("Helvetica", 8)
        c.setFillColor(quiet)
        c.drawString(20 * mm, 12 * mm, FOOTER[0])
        c.drawCentredString(A4[0] / 2, 12 * mm, FOOTER[1])
        c.drawRightString(A4[0] - 20 * mm, 12 * mm, str(c.getPageNumber()))
        c.restoreState()

    story: list[Any] = [Paragraph(_md(form.title), title), Paragraph(_md(form.meta), small), Spacer(1, 4 * mm)]
    for lang in LANGS:
        story.append(CondPageBreak(40 * mm))
        story.append(Paragraph(lang, h2))
        story += [Paragraph(_md(p), body) for p in form.paragraphs[lang]]

    fill: list[Any] = [CondPageBreak(90 * mm), Paragraph("Invullen en tekenen / Fill in and sign", h2)]
    for ch in form.choices:
        block: list[Any] = [Paragraph(f"{_md(ch.label[0])} / {_md(ch.label[1])}", label)]
        if ch.open:
            block.append(writing(ch.key, ch.label[1]))
        for i, (nl, en) in enumerate(ch.options, 1):
            block.append(Field("check", f"{ch.key}_{i}", nl, en, en))
            if ch.text_option == i - 1:
                block.append(written(f"{ch.key}_as", en.rstrip(":")))
        fill.append(KeepTogether(block + [Spacer(1, 2 * mm)]))
    fill += [Spacer(1, 2 * mm), Paragraph(_md(form.declaration[0]), body),
             Paragraph(_md(form.declaration[1]), en_style), Spacer(1, 5 * mm)]
    sign = [b for b in form.blanks if b[0] == "signature"]
    for key, nl, en in [b for b in form.blanks if b[0] != "signature"] + sign:
        fill.append(Field("sign" if key == "signature" else "text", key, nl, en, f"{nl} / {en}"))
        fill.append(Spacer(1, 1.5 * mm))
    story += fill

    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=22 * mm, title=form.title, author="Fix-It Lens · CraftyCast",
                            subject=form.meta, creator="eval/consent/pdf/build.py", invariant=1)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return sig_boxes


def add_signature(path: Path, boxes: list[tuple[int, list[float]]]) -> None:
    """An empty /Sig field over each drawn box: Acrobat offers to sign it; other readers see the box."""
    from pypdf import PdfReader, PdfWriter  # type: ignore[import-not-found,unused-ignore]
    from pypdf.generic import (  # type: ignore[import-not-found,unused-ignore]
        ArrayObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    w = PdfWriter(clone_from=PdfReader(path))
    for i, (page_no, rect) in enumerate(boxes):
        page = w.pages[page_no]
        sig = DictionaryObject({
            NameObject("/FT"): NameObject("/Sig"), NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"), NameObject("/F"): NumberObject(4),
            NameObject("/T"): TextStringObject("signature" if i == 0 else f"signature_{i + 1}"),
            NameObject("/TU"): TextStringObject("Handtekening / Signature"),
            NameObject("/Rect"): ArrayObject([FloatObject(round(v, 2)) for v in rect]),
            NameObject("/P"): page.indirect_reference,
        })
        ref = w._add_object(sig)
        page.setdefault(NameObject("/Annots"), ArrayObject()).append(ref)  # type: ignore[union-attr,unused-ignore]
        w._root_object["/AcroForm"]["/Fields"].append(ref)  # type: ignore[index]
    with path.open("wb") as f:
        w.write(f)


def fields(path: Path) -> dict[str, str]:
    from pypdf import PdfReader  # type: ignore[import-not-found,unused-ignore]

    return {k: str(v.get("/FT")) for k, v in (PdfReader(path).get_fields() or {}).items()}


def check(form: Form, path: Path) -> None:
    """Lists the fields, fills a test copy (in a temporary folder, deleted after) and reads the values back."""
    from pypdf import PdfReader, PdfWriter  # type: ignore[import-not-found,unused-ignore]

    fs = fields(path)
    want = {b[0] for b in form.blanks} | {f"{c.key}_{i}" for c in form.choices for i in range(1, len(c.options) + 1)}
    want |= {f"{c.key}_as" for c in form.choices if c.text_option is not None} | {c.key for c in form.choices if c.open}
    if missing := want - set(fs):
        raise SystemExit(f"{path.name}: fields missing {sorted(missing)}")
    if fs.get("signature") != "/Sig":
        raise SystemExit(f"{path.name}: no empty signature field")
    for k, t in sorted(fs.items()):
        print(f"  {k:22} {t}")
    values = {k: "Test" for k, t in fs.items() if t == "/Tx"}
    ticks = {k for k, t in fs.items() if t == "/Btn" and k.endswith("_1")}
    with tempfile.TemporaryDirectory() as tmp:
        w = PdfWriter(clone_from=PdfReader(path))
        for page in w.pages:
            on = {}
            for a in page.get("/Annots") or []:
                a = a.get_object()
                if a.get("/T") in ticks:
                    on[a["/T"]] = next(n for n in a["/AP"]["/N"] if n != "/Off")
            if page.get("/Annots"):
                w.update_page_form_field_values(page, {**values, **on}, auto_regenerate=False)
        copy = Path(tmp) / "filled.pdf"
        with copy.open("wb") as f:
            w.write(f)
        got = {k: v.get("/V") for k, v in (PdfReader(copy).get_fields() or {}).items()}
        bad = [k for k in values if got.get(k) != "Test"] + [k for k in ticks if got.get(k) in (None, "/Off")]
        if bad:
            raise SystemExit(f"{path.name}: the test copy did not keep {bad}")
    print(f"  test copy: {len(values)} text fields and {len(ticks)} boxes filled and read back; deleted")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="eval/consent/pdf/build.py")
    ap.add_argument("--check", action="store_true", help="list the fields and fill a test copy")
    a = ap.parse_args(argv)
    for name in FORMS:
        form = parse(name)
        out = HERE / f"{name}.pdf"
        add_signature(out, render(form, out))
        print(f"{out}  ({len(fields(out))} fields)")
        if a.check:
            check(form, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
