"""Write the README's "Five models" figure, docs/readme/five-models-{light,dark}.svg: what flows from the phone to
the server, and from the server to each model.

    uv run python tools/make_readme_models.py --fonts <dir with the site's woff2 files>

It shares the "How it works" figure's palettes, fonts and style (tools/make_readme_diagram.py), so the two read as one
set. That figure says who decides; this one says where each call goes. tests/l0/test_readme.py checks the committed
files carry exactly this layout's text, that every model id is the one config/models.toml pins, and that they load
nothing from outside.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import make_readme_diagram as base  # noqa: E402

OUT = base.OUT
W = base.W
L, LW = 48, 300          # the phone and the server, stacked
R, RW = 472, 400         # the models, one row each
TRUNK = 410              # where the server's calls fan out to the models


@dataclass(frozen=True)
class Model:
    y: int
    title: str
    model_id: str        # as config/models.toml pins it
    roles: tuple[str, ...]
    sub: str             # its role, in one line
    flow: str            # what goes in, and what comes back
    local: bool = False
    h: int = 84

    @property
    def mid(self) -> int:
        return self.y + self.h // 2


TITLE = "Five models. One server calls them all."
API_LABEL = "META'S API"
LOCAL_LABEL = "YOUR OWN NETWORK · LOCAL MODE"
MODELS = [
    Model(172, "Muse Wonder", "muse-wonder-1.0", ("appliance_file",),
          "reads the whole manual and drafts the card", "MANUAL PDF → DRAFT CARD; CODE CHECKS EVERY QUOTE"),
    Model(268, "Muse Spark", "muse-spark-1.3", ("guide", "judge", "locate", "plate"),
          "guides, boxes the part, judges a step, reads a plate", "FRAMES + CARD → SENTENCES, BOXES, FINDINGS"),
    Model(364, "SAM 3.1", "sam-3.1", ("sam", "sam_video"),
          "outlines the part, and tracks it as a second witness", "FRAME + BOX → MASK · VIDEO → TRACK"),
    Model(460, "Muse Voice", "muse-voice-transcribe-1.0", ("transcribe",),
          "turns hold-to-talk into text", "WAV → TEXT; CODE STOPS ON EMERGENCY WORDS"),
    Model(596, "Muse Glimmer", "muse-glimmer:30b", ("guide", "judge", "locate", "plate", "appliance_file"),
          "the same roles through ollama, on a GPU you own", "AFTER TWO FAILED API CALLS · NO SAM, NO VOICE IN",
          local=True),
]


@dataclass(frozen=True)
class Node:
    y: int
    h: int
    title: str
    lines: tuple[str, ...]
    mark: str            # the folder that does it
    accent: str


PHONE = Node(172, 112, "Your phone", ("the camera, hold to talk, taps", "speaks in its own voice"),
             "PHONE/ · IN THE BROWSER", "ink")
SERVER = Node(388, 132, "Fix-It Lens server", ("every model call goes through calls/",
                                               "code decides: G0, the rules,", "the verdict, the card's quotes"),
              "APP/ · CALLS/ · RULES/", "green")
TO_SERVER = ["↓ FRAMES · AUDIO · TAPS"]
TO_PHONE = ["↑ SENTENCES · OUTLINES", "  VERDICTS · STOPS"]
WIRE = "ONE WEBSOCKET: /WS"
LEGEND = [("blue", "A MODEL PROPOSES"), ("green", "CODE DECIDES"), ("ink", "YOUR DEVICE")]
H = 760


def texts() -> list[tuple[str, str]]:
    """Every string the figure draws, with its font key: the test compares the committed files against this."""
    mono = "JetBrains Mono 500"
    out = [("Fraunces 650", TITLE), (mono, "§"), (mono, "WHERE EACH CALL GOES"), (mono, API_LABEL),
           (mono, LOCAL_LABEL), (mono, WIRE)]
    for m in MODELS:
        out += [("Inter 600", m.title), (mono, m.model_id.upper()), ("Inter 400", m.sub), (mono, m.flow)]
    for n in (PHONE, SERVER):
        out += [("Inter 600", n.title), (mono, n.mark)] + [("Inter 400", t) for t in n.lines]
    out += [(mono, t) for t in TO_SERVER + TO_PHONE]
    out += [(mono, t) for _, t in LEGEND]
    return out


def node(n: Node) -> str:
    lines = "".join(f'<text class="sub t2" x="{L + 16}" y="{n.y + 48 + 18 * i}">{escape(t)}</text>'
                    for i, t in enumerate(n.lines))
    return (f'<rect class="sheet ink" x="{L}" y="{n.y}" width="{LW}" height="{n.h}" stroke-width="1.5"/>'
            f'<path class="{n.accent}" d="M{L} {n.y + 1.5}h{LW}" stroke-width="4"/>'
            f'<text class="lab t" x="{L + 16}" y="{n.y + 26}">{escape(n.title)}</text>{lines}'
            f'<text class="mono {"t3" if n.accent == "ink" else "f" + n.accent}" x="{L + 16}" '
            f'y="{n.y + n.h - 14}">{escape(n.mark)}</text>')


def model(m: Model) -> str:
    dash = ' stroke-dasharray="5 5"' if m.local else ""
    return (f'<rect class="sheet ink" x="{R}" y="{m.y}" width="{RW}" height="{m.h}" stroke-width="1.5"{dash}/>'
            + ("" if m.local else f'<path class="blue" d="M{R} {m.y + 1.5}h{RW}" stroke-width="4"/>') +
            f'<text class="lab t" x="{R + 16}" y="{m.y + 26}">{escape(m.title)}</text>'
            f'<text class="mono fblue" x="{R + RW - 16}" y="{m.y + 26}" text-anchor="end">'
            f'{escape(m.model_id.upper())}</text>'
            f'<text class="sub t2" x="{R + 16}" y="{m.y + 46}">{escape(m.sub)}</text>'
            f'<text class="mono t3" x="{R + 16}" y="{m.y + m.h - 14}">{escape(m.flow)}</text>')


def body() -> str:
    parts = [f'<rect class="paper" width="{W}" height="{H}"/><rect width="{W}" height="{H}" fill="url(#g)"/>',
             '<text class="mono fred" x="48" y="52">§</text>'
             '<text class="mono t3" x="66" y="52">WHERE EACH CALL GOES</text>',
             f'<text class="title t" x="48" y="90">{escape(TITLE)}</text>',
             f'<path class="ink" d="M32 128V112H48M{W - 48} 112H{W - 32}V128M32 {H - 40}V{H - 24}H48'
             f'M{W - 48} {H - 24}H{W - 32}V{H - 40}" fill="none" stroke-width="1.5"/>',
             f'<text class="mono t3" x="{R}" y="{MODELS[0].y - 14}">{API_LABEL}</text>',
             f'<path class="rule" d="M{R + round(7.5 * len(API_LABEL)) + 14} {MODELS[0].y - 18}H{R + RW}" '
             f'stroke-width="1"/>',
             f'<text class="mono t3" x="{R}" y="{MODELS[-1].y - 14}">{LOCAL_LABEL}</text>',
             f'<path class="rule" d="M{R + round(7.5 * len(LOCAL_LABEL)) + 14} {MODELS[-1].y - 18}H{R + RW}" '
             f'stroke-width="1"/>']
    parts += [node(PHONE), node(SERVER)] + [model(m) for m in MODELS]
    # the phone and the server: one socket, both ways
    top, bot = PHONE.y + PHONE.h, SERVER.y
    xd, xu = L + 24, L + 44     # down to the server, up to the phone
    parts += [base.arrow(f"M{xd} {top + 4}V{bot - 4}"), base.arrow(f"M{xu} {bot - 4}V{top + 4}")]
    parts.append(f'<text class="mono t3" x="{xu + 20}" y="{top + 26}">{WIRE}</text>')
    for i, t in enumerate(TO_SERVER):
        parts.append(f'<text class="mono fblue" x="{xu + 20}" y="{top + 48 + 16 * i}" xml:space="preserve">'
                     f'{escape(t)}</text>')
    for i, t in enumerate(TO_PHONE):
        parts.append(f'<text class="mono fgreen" x="{xu + 20}" y="{top + 68 + 16 * i}" xml:space="preserve">'
                     f'{escape(t)}</text>')
    # the server's calls fan out: one trunk, a branch into each model (dashed to the local one)
    sy = SERVER.y + SERVER.h // 2
    api = [m for m in MODELS if not m.local]
    loc = MODELS[-1]
    parts.append(f'<path class="ink" d="M{L + LW} {sy}H{TRUNK}M{TRUNK} {api[0].mid}V{api[-1].mid}" fill="none" '
                 f'stroke-width="1.5"/>')
    parts += [base.arrow(f"M{TRUNK} {m.mid}H{R - 4}") for m in api]
    parts.append(base.arrow(f"M{TRUNK} {api[-1].mid}V{loc.mid}H{R - 4}", dashed=True))
    x = 48
    for accent, label in LEGEND:
        parts.append(f'<path class="{accent}" d="M{x} {H - 60}h24" stroke-width="4"/>'
                     f'<text class="mono t3" x="{x + 32}" y="{H - 56}">{label}</text>')
        x += 32 + round(7.5 * len(label)) + 36
    return "\n".join(p for p in parts if p)


def svg(theme: str, fonts: dict[tuple[str, str], bytes]) -> str:
    alt = ("Where each call goes: your phone sends camera frames, hold-to-talk audio and taps to the Fix-It Lens "
           "server over one WebSocket, and gets back sentences, outlines, verdicts and stops; it speaks in its own "
           "voice. Every model call goes through the server, and code there decides. Muse Wonder reads the manual "
           "and drafts the card. Muse Spark guides, boxes the part, judges a step and reads a plate. SAM 3.1 "
           "outlines the part and tracks it as a second witness. Muse Voice turns hold-to-talk into text. In local "
           "mode, after two failed API calls, Muse Glimmer takes the same roles on a GPU on your own network, with "
           "no SAM and no voice input.")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">'
            f"<title>{escape(alt)}</title>\n"
            "<!-- Generated by tools/make_readme_models.py. Fonts: Fraunces, Inter and JetBrains Mono, SIL Open Font "
            "License 1.1, subset. -->\n"
            f"<style>{base.css(theme, fonts)}</style>\n"
            '<defs><pattern id="g" width="24" height="24" patternUnits="userSpaceOnUse"><path class="grid" '
            'd="M24 0H0V24" fill="none" stroke-width="1"/></pattern><marker id="a" viewBox="0 0 10 10" refX="9" '
            'refY="5" markerWidth="9" markerHeight="9" orient="auto" markerUnits="userSpaceOnUse"><path class="ink" '
            'd="M1 1L9 5L1 9" fill="none" stroke-width="1.5"/></marker></defs>\n'
            f"{body()}\n</svg>\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonts", type=Path, required=True, help="the folder with the site's woff2 files")
    args = ap.parse_args()
    by_font: dict[tuple[str, str], str] = {}
    for key, t in texts():
        fam, wt = key.rsplit(" ", 1)
        by_font[(fam, wt)] = by_font.get((fam, wt), "") + t
    by_font[("Inter", "400")] += "".join(sorted({c for _, t in texts() for c in t}))  # fallback for any text
    fonts = {k: base.subset(args.fonts / base.FONTS[k], by_font[k]) for k in base.FONTS}
    for theme in base.PALETTES:
        (OUT / f"five-models-{theme}.svg").write_text(svg(theme, fonts))
    print("wrote docs/readme/five-models-{light,dark}.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
