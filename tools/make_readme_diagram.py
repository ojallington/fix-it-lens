"""Write the README's "How it works" figure, docs/readme/how-it-works-{light,dark}.svg, in the site's style.

    uv run python tools/make_readme_diagram.py --fonts <dir with the site's woff2 files>

One layout, two palettes (paper and blueprint); the README picks one with a <picture>. An SVG shown through <img>
cannot load web fonts, so each file embeds subsets of Fraunces, Inter and JetBrains Mono (all SIL OFL 1.1) as data:
URIs, cut to the characters it uses with fontTools (run through uvx; not a project dependency). The text is the
README table's, shorter; the table stays the accessible, searchable version. tests/l0/test_readme.py checks the
committed files carry exactly this layout's text, name only files that exist, and load nothing from outside.
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/readme"
W = 920
FONTS = {  # family, weight -> file in the site's fonts folder
    ("Fraunces", "650"): "fraunces-var.woff2",
    ("Inter", "400"): "inter-latin-400-normal.woff2",
    ("Inter", "600"): "inter-latin-600-normal.woff2",
    ("JetBrains Mono", "500"): "jetbrains-mono-latin-500-normal.woff2",
}
PALETTES = {
    "light": {"bg": "#f2ede2", "sheet": "#faf7f0", "ink": "#1c1a16", "ink2": "#4a463d", "ink3": "#7d7768",
              "rule": "rgba(28,26,22,.16)", "grid": "rgba(42,73,216,.06)", "blue": "#2a49d8", "green": "#1d7a4b",
              "red": "#d9381e", "amber": "#a96500"},
    "dark": {"bg": "#0d1726", "sheet": "#15253d", "ink": "#e8eef8", "ink2": "#b3bfd2", "ink3": "#7c8aa2",
             "rule": "rgba(232,238,248,.18)", "grid": "rgba(127,162,255,.07)", "blue": "#86a6ff", "green": "#4fd69a",
             "red": "#ff6b4f", "amber": "#ffb84d"},
}

# Columns and rows of the grid the boxes sit on.
COL = [48, 352, 656]
BW, BH = 216, 76


@dataclass(frozen=True)
class Box:
    col: int
    y: int
    title: str
    sub: str
    mark: str           # the small mono line: a model id and role, or the file that decides
    accent: str         # blue: a Meta model proposes; green: code checks; red: code stops
    h: int = BH

    @property
    def x(self) -> int:
        return COL[self.col]

    @property
    def mid(self) -> int:
        return self.y + self.h // 2


TITLE = "Meta's models propose. Code decides."
PHASES = [(146, "01", "BEFORE THE JOB"), (394, "02", "DURING THE JOB"), (714, "03", "IS THE STEP DONE?"),
          (938, "04", "NO CONNECTION")]

PLATE = Box(0, 176, "Muse Spark", "reads the type plate", "MUSE-SPARK-1.3 · PLATE", "blue")
PLATE_CODE = Box(1, 176, "Look-alikes asked", "O or 0 is one tap; no serial", "INGEST/PLATE.PY", "green")
WONDER = Box(0, 272, "Muse Wonder", "reads the whole manual", "MUSE-WONDER-1.0", "blue")
CITE = Box(1, 272, "Every quote checked", "against its own page", "INGEST/CITATION.PY", "green")
CARD = Box(2, 272, "Checked card", "short quotes, page numbers", "CARDS/", "green")
GUIDE = Box(0, 412, "Muse Spark", "guides, a sentence at a time", "MUSE-SPARK-1.3 · GUIDE", "blue")
G0 = Box(1, 412, "G0 holds a sentence", "that sends you to a red zone", "RULES/G0.PY", "red")
LOCATE = Box(0, 508, "Muse Spark", "boxes the part", "MUSE-SPARK-1.3 · LOCATE", "blue")
SAM = Box(1, 508, "SAM 3.1", "masks it on your screen", "SAM-3.1 · SAM", "blue")
VOICE = Box(0, 604, "Muse Voice", "hold to talk", "MUSE-VOICE-TRANSCRIBE-1.0", "blue")
ENGINE = Box(1, 604, "Emergency words stop", "a gas smell, an alarm, fire", "RULES/ENGINE.PY", "red")
JUDGE = Box(0, 744, "Muse Spark judge", "four frames, the step's lines", "MUSE-SPARK-1.3 · JUDGE", "blue")
SAMV = Box(0, 840, "SAM 3.1 on video", "tracks the part: a witness", "SAM-3.1 · SAM_VIDEO", "blue")
VERDICT = Box(2, 744, "The verdict, in code", "camera pass: both agree;", "APP/VERDICT.PY · COMBINE", "green",
              h=172)
VERDICT_SUB2 = "no model writes a verdict"
OUTCOMES = [("green", "DONE"), ("amber", "NOT DONE"), ("amber", "CAN'T SEE: MOVE CLOSER")]   # app/verdict.py Result
GLIMMER = Box(0, 962, "Muse Glimmer on your own GPU", "guide, judge, locate, plate and manual, through ollama, "
              "after two failed API calls; optical flow is the witness", "MUSE-GLIMMER:30B · LOCAL", "blue",
              h=64)
BOXES = [PLATE, PLATE_CODE, WONDER, CITE, CARD, GUIDE, G0, LOCATE, SAM, VOICE, ENGINE, JUDGE, SAMV, VERDICT]
LEGEND = [("blue", "A META MODEL PROPOSES"), ("green", "CODE CHECKS"), ("red", "CODE STOPS"),
          ("amber", "NOT YET, SHOWN")]
H = 1112


def texts() -> list[tuple[str, str]]:
    """Every string the figure draws, with its font key: the test compares the committed files against this."""
    out = [("Fraunces 650", TITLE), ("JetBrains Mono 500", "§"), ("JetBrains Mono 500", "HOW IT WORKS")]
    for _, n, name in PHASES:
        out += [("JetBrains Mono 500", n), ("JetBrains Mono 500", name)]
    for b in [*BOXES, GLIMMER]:
        out += [("Inter 600", b.title), ("Inter 400", b.sub), ("JetBrains Mono 500", b.mark)]
    out += [("Inter 400", VERDICT_SUB2), ("JetBrains Mono 500", "THE PART'S MASK")]
    out += [("JetBrains Mono 500", t) for _, t in OUTCOMES]
    out += [("JetBrains Mono 500", t) for _, t in LEGEND]
    return out


def arrow(d: str, dashed: bool = False) -> str:
    dash = ' stroke-dasharray="5 5"' if dashed else ""
    return f'<path class="ink" d="{d}" fill="none" stroke-width="1.5"{dash} marker-end="url(#a)"/>'


def box(b: Box) -> str:
    sub2 = ""
    if b is VERDICT:
        sub2 = f'<text class="sub t2" x="{b.x + 16}" y="{b.y + 62}">{escape(VERDICT_SUB2)}</text>'
        for i, (accent, label) in enumerate(OUTCOMES):
            y = b.y + 92 + 20 * i
            sub2 += (f'<path class="{accent}" d="M{b.x + 16} {y - 4}h10" stroke-width="4"/>'
                     f'<text class="mono f{accent}" x="{b.x + 34}" y="{y}">{escape(label)}</text>')
    mark_y = b.y + b.h - 14
    return (f'<rect class="sheet ink" x="{b.x}" y="{b.y}" width="{BW}" height="{b.h}" stroke-width="1.5"/>'
            f'<path class="{b.accent}" d="M{b.x} {b.y + 1.5}h{BW}" stroke-width="4"/>'
            f'<text class="lab t" x="{b.x + 16}" y="{b.y + 26}">{escape(b.title)}</text>'
            f'<text class="sub t2" x="{b.x + 16}" y="{b.y + 44}">{escape(b.sub)}</text>{sub2}'
            f'<text class="mono f{b.accent}" x="{b.x + 16}" y="{mark_y}">{escape(b.mark)}</text>')


def right(a: Box, b: Box) -> str:
    return arrow(f"M{a.x + BW} {a.mid}H{b.x - 4}")


def body() -> str:
    g = GLIMMER
    parts = [f'<rect class="paper" width="{W}" height="{H}"/><rect width="{W}" height="{H}" fill="url(#g)"/>',
             '<text class="mono fred" x="48" y="52">§</text><text class="mono t3" x="66" y="52">HOW IT WORKS</text>',
             f'<text class="title t" x="48" y="90">{escape(TITLE)}</text>',
             # crop marks outside the figure's corners
             f'<path class="ink" d="M32 128V112H48M{W - 48} 112H{W - 32}V128M32 {H - 40}V{H - 24}H48'
             f'M{W - 48} {H - 24}H{W - 32}V{H - 40}" fill="none" stroke-width="1.5"/>']
    for y, n, name in PHASES:
        parts.append(f'<text class="mono fred" x="48" y="{y}">{n}</text>'
                     f'<text class="mono t3" x="76" y="{y}">{escape(name)}</text>'
                     f'<path class="rule" d="M{76 + round(7.5 * len(name)) + 14} {y - 4}H{W - 48}" stroke-width="1"/>')
    parts += [box(b) for b in BOXES]
    parts += [right(PLATE, PLATE_CODE), right(WONDER, CITE), right(CITE, CARD), right(GUIDE, G0),
              right(LOCATE, SAM), right(VOICE, ENGINE), right(JUDGE, VERDICT), right(SAMV, VERDICT),
              # the card feeds the guide: down from the card, left along the gap, past the phase label, into the guide
              arrow(f"M{CARD.x + BW // 2} {CARD.y + BH}V{CARD.y + BH + 16}H{GUIDE.x + BW - 40}V{GUIDE.y - 4}"),
              # SAM's mask is the verdict's evidence of the part
              arrow(f"M{SAM.x + BW} {SAM.mid}H{VERDICT.x + BW // 2}V{VERDICT.y - 4}"),
              f'<text class="mono t3" x="{SAM.x + BW + 16}" y="{SAM.mid - 10}">THE PART\'S MASK</text>']
    parts.append(f'<rect class="sheet ink" x="{g.x}" y="{g.y}" width="{COL[2] + BW - g.x}" height="{g.h}" '
                 f'stroke-width="1.5" stroke-dasharray="5 5"/>'
                 f'<text class="lab t" x="{g.x + 16}" y="{g.y + 24}">{escape(g.title)}</text>'
                 f'<text class="mono fblue" x="{COL[2] + BW - 16}" y="{g.y + 24}" text-anchor="end">'
                 f'{escape(g.mark)}</text>'
                 f'<text class="sub t2" x="{g.x + 16}" y="{g.y + 46}">{escape(g.sub)}</text>')
    x = 48
    for accent, label in LEGEND:
        parts.append(f'<path class="{accent}" d="M{x} {H - 60}h24" stroke-width="4"/>'
                     f'<text class="mono t3" x="{x + 32}" y="{H - 56}">{label}</text>')
        x += 32 + round(7.5 * len(label)) + 36
    return "\n".join(parts)


def css(theme: str, fonts: dict[tuple[str, str], bytes]) -> str:
    p = PALETTES[theme]
    faces = "".join(
        f'@font-face{{font-family:"{fam}";font-weight:{"100 900" if fam == "Fraunces" else wt};'
        f'src:url(data:font/woff2;base64,{base64.b64encode(data).decode()}) format("woff2")}}'
        for (fam, wt), data in fonts.items())
    return (faces +
            f".paper{{fill:{p['bg']}}}.sheet{{fill:{p['sheet']}}}.grid{{stroke:{p['grid']}}}.ink{{stroke:{p['ink']}}}"
            f".rule{{stroke:{p['rule']}}}.t{{fill:{p['ink']}}}.t2{{fill:{p['ink2']}}}.t3{{fill:{p['ink3']}}}"
            + "".join(f".{c}{{stroke:{p[c]}}}.f{c}{{fill:{p[c]}}}" for c in ("blue", "green", "red", "amber")) +
            'text{font-family:Inter,system-ui,sans-serif}'
            '.mono{font-family:"JetBrains Mono",ui-monospace,monospace;font-weight:500;font-size:11px;'
            'letter-spacing:.08em}'
            '.title{font-family:Fraunces,Georgia,serif;font-weight:650;font-size:30px;letter-spacing:-.02em}'
            '.lab{font-size:15px;font-weight:600}.sub{font-size:13px}')


def svg(theme: str, fonts: dict[tuple[str, str], bytes]) -> str:
    alt = ("How it works: Meta's models propose and code decides. Before the job, Muse Spark reads the plate and "
           "Muse Wonder the manual, and code checks both into a card. During the job, Muse Spark guides, boxes the "
           "part for SAM 3.1, and Muse Voice listens; G0 and the rule engine can stop. Whether a step is done is "
           "decided in code from the judge and a second witness. With no connection, Muse Glimmer runs locally.")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">'
            f"<title>{escape(alt)}</title>\n"
            "<!-- Generated by tools/make_readme_diagram.py. Fonts: Fraunces, Inter and JetBrains Mono, SIL Open Font "
            "License 1.1, subset. -->\n"
            f"<style>{css(theme, fonts)}</style>\n"
            '<defs><pattern id="g" width="24" height="24" patternUnits="userSpaceOnUse"><path class="grid" '
            'd="M24 0H0V24" fill="none" stroke-width="1"/></pattern><marker id="a" viewBox="0 0 10 10" refX="9" '
            'refY="5" markerWidth="9" markerHeight="9" orient="auto" markerUnits="userSpaceOnUse"><path class="ink" '
            'd="M1 1L9 5L1 9" fill="none" stroke-width="1.5"/></marker></defs>\n'
            f"{body()}\n</svg>\n")


def subset(src: Path, text: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "s.woff2"
        subprocess.run(["uvx", "--from", "fonttools[woff]", "pyftsubset", str(src), f"--text={text}",
                        "--flavor=woff2", "--layout-features=kern,liga", f"--output-file={out}"], check=True,
                       cwd=ROOT)
        return out.read_bytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonts", type=Path, required=True, help="the folder with the site's woff2 files")
    args = ap.parse_args()
    by_font: dict[tuple[str, str], str] = {}
    for key, t in texts():
        fam, wt = key.rsplit(" ", 1)
        by_font[(fam, wt)] = by_font.get((fam, wt), "") + t
    by_font[("Inter", "400")] += "".join(sorted({c for _, t in texts() for c in t}))  # fallback for any text
    fonts = {k: subset(args.fonts / FONTS[k], by_font[k]) for k in FONTS}
    for theme in PALETTES:
        (OUT / f"how-it-works-{theme}.svg").write_text(svg(theme, fonts))
    print("wrote docs/readme/how-it-works-{light,dark}.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
