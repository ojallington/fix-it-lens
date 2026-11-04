"""Builds every Fix-It Lens brand asset from one drawing of the mark (the lock-on: two crop corners on the part the
camera outlines). Writes next to itself; BRAND.md says what each file is for.

    uv run --with uharfbuzz --with fonttools --with brotli python brand/build.py

Needs rsvg-convert and ImageMagick (magick) for the PNGs and the .ico, and Fraunces as a variable TTF for the wordmark,
which is shaped with HarfBuzz and written out as outlines, so no SVG here depends on a font being installed.
"""

import math
import subprocess
from pathlib import Path

OUT = Path(__file__).parent
MONO = OUT.parent / "phone/src/fonts/jetbrains-mono-latin-500-normal.woff2"
FRAUNCES = Path.home() / ".local/share/fonts/crafty-cast/Fraunces[SOFT,WONK,opsz,wght].ttf"

# The tokens (phone/src/style.css). The accent has one value per ground.
INK = "#15130f"
PAPER = "#f2ede2"
FG_ON_INK = "#f1ece0"
FG_ON_PAPER = "#1c1a16"
ACCENT_ON_INK = "#93a8ff"
ACCENT_ON_PAPER = "#2a49d8"
FAINT_ON_INK = "#958e7e"


def cap_path(cx, cy, r, lug_w, lug_h, lug_r):
    """The part: a round bayonet cap with a lug either side, as one closed outline."""
    hy = lug_h / 2
    x = math.sqrt(r * r - hy * hy)
    lr = lug_r
    return (
        f"M{cx + x:.2f} {cy - hy:g}H{cx + r + lug_w - lr:g}a{lr:g} {lr:g} 0 0 1 {lr:g} {lr:g}V{cy + hy - lr:g}"
        f"a{lr:g} {lr:g} 0 0 1 -{lr:g} {lr:g}H{cx + x:.2f}A{r:g} {r:g} 0 0 1 {cx - x:.2f} {cy + hy:g}"
        f"H{cx - r - lug_w + lr:g}a{lr:g} {lr:g} 0 0 1 -{lr:g} -{lr:g}"
        f"V{cy - hy + lr:g}a{lr:g} {lr:g} 0 0 1 {lr:g} -{lr:g}"
        f"H{cx - x:.2f}A{r:g} {r:g} 0 0 1 {cx + x:.2f} {cy - hy:g}Z"
    )


def mark(fg, acc):
    """The mark on a 64-unit grid, 6 units of air inside the square: two crop corners, the cap outlined, its bore.
    The cap is turned 45 degrees so its lugs point into the two open corners: a part you twist, not an eye."""
    return (
        f'<path d="M6 22V6h16M58 42v16H42" fill="none" stroke="{fg}" stroke-width="5.5" stroke-linecap="square"/>'
        f'<path d="{cap_path(32, 32, 12, 5, 8, 1.5)}" transform="rotate(-45 32 32)" fill="{acc}" fill-opacity="0.3" '
        f'stroke="{acc}" stroke-width="4.5" stroke-linejoin="round"/>'
        f'<circle cx="32" cy="32" r="3.6" fill="{acc}"/>'
    )


# The 16 px mark, pixel by pixel: (x, y, w, h) runs on the 16 grid, so it stays sharp at 1x and 2x.
CORNERS16 = [(2, 2, 4, 1), (2, 3, 1, 3), (10, 13, 4, 1), (13, 10, 1, 3)]
CAP16 = [
    (6, 4, 4, 1),
    (5, 5, 1, 1),
    (10, 5, 1, 1),
    (4, 6, 1, 4),
    (11, 6, 1, 4),
    (5, 10, 1, 1),
    (10, 10, 1, 1),
    (6, 11, 4, 1),
    (11, 4, 2, 2),
    (3, 10, 2, 2),
    (7, 7, 2, 2),
]


def mark16(fg, acc):
    """Its own drawing for 16 px: one-pixel corners, the cap outlined with its lugs and bore."""

    def run(rects, colour):
        return "".join(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{colour}"/>' for x, y, w, h in rects)

    return run(CORNERS16, fg) + run(CAP16, acc)


def svg(view, body, size=None, title="Fix-It Lens"):
    wh = f' width="{size[0]}" height="{size[1]}"' if size else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}"{wh}><title>{title}</title>{body}</svg>\n'


def outline(text, font_file, variations, size, x, baseline, tracking_em, fill):
    """Text as one SVG path, shaped and kerned by HarfBuzz, so it renders the same without the font installed."""
    import io

    import uharfbuzz as hb
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    data = Path(font_file).read_bytes()
    if Path(font_file).suffix == ".woff2":
        raw = io.BytesIO()
        tt = TTFont(io.BytesIO(data))
        tt.flavor = None
        tt.save(raw)
        data = raw.getvalue()
    face = hb.Face(hb.Blob(data))
    font = hb.Font(face)
    if variations:
        font.set_variations(variations)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": True})
    s = size / face.upem
    track = tracking_em * face.upem
    pen = SVGPathPen(None)
    pen_x = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        # Font units are y-up; flip into SVG's y-down at the baseline.
        tp = TransformPen(pen, (s, 0, 0, -s, x + (pen_x + pos.x_offset) * s, baseline - pos.y_offset * s))
        font.draw_glyph_with_pen(info.codepoint, tp)
        pen_x += pos.x_advance + track
    return f'<path d="{pen.getCommands()}" fill="{fill}"/>', (pen_x - track) * s


def wordmark(fill, size=38, x=0, baseline=0):
    """'Fix-It Lens' in Fraunces 600 at -0.01em, like the app's .brand."""
    return outline(
        "Fix-It Lens", FRAUNCES, {"wght": 600, "opsz": 40, "SOFT": 0, "WONK": 0}, size, x, baseline, -0.01, fill
    )


def label(text, x, baseline, fill):
    """A mono label as on the app's kickers: JetBrains Mono 500, uppercase, 0.08em."""
    return outline(text, MONO, None, 22, x, baseline, 0.08, fill)[0]


def lockup(fg, acc):
    """Mark and wordmark, transparent. The wordmark's cap height sits inside the corners' span."""
    word, w = wordmark(fg, 38, 78, 45)
    return svg(f"0 0 {78 + math.ceil(w) + 2} 64", mark(fg, acc) + word)


def tile(body, rx=14, scale=0.75, bg=INK):
    off = 32 * (1 - scale)
    return (
        f'<rect width="64" height="64" rx="{rx}" fill="{bg}"/>'
        f'<g transform="translate({off:g} {off:g}) scale({scale})">{body}</g>'
    )


def png(svg_path, out, w, h=None):
    subprocess.run(["rsvg-convert", "-w", str(w), "-h", str(h or w), str(svg_path), "-o", str(out)], check=True)


def write(rel, text):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def main():
    on_ink = mark(FG_ON_INK, ACCENT_ON_INK)
    on_paper = mark(FG_ON_PAPER, ACCENT_ON_PAPER)

    # The masters: transparent, one per ground.
    write("mark.svg", svg("0 0 64 64", on_ink))
    write("mark-light.svg", svg("0 0 64 64", on_paper))
    write(
        "mark-16.svg",
        svg(
            "0 0 16 16",
            f'<rect width="16" height="16" rx="3" fill="{INK}"/>' + mark16(FG_ON_INK, ACCENT_ON_INK),
            (16, 16),
        ),
    )
    write("lockup.svg", lockup(FG_ON_INK, ACCENT_ON_INK))
    write("lockup-light.svg", lockup(FG_ON_PAPER, ACCENT_ON_PAPER))

    # Web and PWA, named as phone/public names them.
    write("web/favicon.svg", (OUT / "mark-16.svg").read_text())
    icon = write("web/icon.svg", svg("0 0 64 64", tile(on_ink), (512, 512)))
    png(icon, OUT / "web/icon-192.png", 192)
    png(icon, OUT / "web/icon-512.png", 512)
    # Maskable: full bleed, the mark inside the middle 60% so an 80% circle crop never touches it.
    maskable = write("web/icon-maskable.svg", svg("0 0 64 64", tile(on_ink, rx=0, scale=0.6), (512, 512)))
    png(maskable, OUT / "web/icon-maskable-512.png", 512)
    # iOS rounds the corners itself and shows transparency as black, so full bleed.
    apple = write("web/apple-touch-icon.svg", svg("0 0 64 64", tile(on_ink, rx=0, scale=0.7), (180, 180)))
    png(apple, OUT / "web/apple-touch-icon.png", 180)
    ico_parts = []
    for px, src in ((16, OUT / "mark-16.svg"), (32, OUT / "mark-16.svg"), (48, icon)):
        p = OUT / f"web/.ico-{px}.png"
        png(src, p, px)
        ico_parts.append(p)
    subprocess.run(["magick", *map(str, ico_parts), str(OUT / "web/favicon.ico")], check=True)
    for p in ico_parts:
        p.unlink()

    # Home Assistant integration: transparent and trimmed to the drawing, light-UI and dark-UI versions.
    trim = "3 3 58 58"
    for name, body in (("icon", on_paper), ("dark_icon", on_ink)):
        src = write(f"ha/{name}.svg", svg(trim, body))
        png(src, OUT / f"ha/{name}.png", 256)
        png(src, OUT / f"ha/{name}@2x.png", 512)
    for name, fg, acc in (("logo", FG_ON_PAPER, ACCENT_ON_PAPER), ("dark_logo", FG_ON_INK, ACCENT_ON_INK)):
        src = write(f"ha/{name}.svg", lockup(fg, acc))
        subprocess.run(["rsvg-convert", "-h", "128", str(src), "-o", str(OUT / f"ha/{name}.png")], check=True)
        subprocess.run(["rsvg-convert", "-h", "256", str(src), "-o", str(OUT / f"ha/{name}@2x.png")], check=True)

    # Home Assistant add-on: icon 128 square; logo about 250 x 100. The store shows both on either theme, so they
    # carry their own ink plate.
    addon_icon = write("addon/icon.svg", svg("0 0 64 64", tile(on_ink, rx=12, scale=0.78)))
    png(addon_icon, OUT / "addon/icon.png", 128)
    word, w = wordmark(FG_ON_INK, 38, 78, 45)
    lw = 78 + math.ceil(w) + 2
    plate = f'<rect x="-14" y="-18" width="{lw + 28}" height="100" rx="8" fill="{INK}"/>' + on_ink + word
    addon_logo = write("addon/logo.svg", svg(f"-14 -18 {lw + 28} 100", plate))
    subprocess.run(["rsvg-convert", "-w", "250", str(addon_logo), "-o", str(OUT / "addon/logo.png")], check=True)

    # OG image, 1200 x 630: the lockup large on ink, with the line the product stands on.
    word, w = wordmark(FG_ON_INK, 96, 0, 0)
    og = (
        f'<rect width="1200" height="630" fill="{INK}"/>'
        f'<g transform="translate(96 150) scale(5.25)">{on_ink}</g>'
        f'<g transform="translate(500 300)">{word}</g>'
        f'<path d="M500 352h604" stroke="{FG_ON_INK}" stroke-opacity="0.16" stroke-width="2"/>'
        + label("READS THE MANUAL · SHOWS THE PART", 500, 404, FAINT_ON_INK)
        + label("CHECKS THE STEP · MARKS THE RED ZONE", 500, 440, FAINT_ON_INK)
    )
    og_svg = write("og/og.svg", svg("0 0 1200 630", og, (1200, 630)))
    png(og_svg, OUT / "og/og.png", 1200, 630)

    # Video end card: the transparent lockup for compositing, and a 1920 x 1080 card to drop in as is.
    write("video/endcard-mark.svg", svg("0 0 64 64", on_ink))
    subprocess.run(
        ["rsvg-convert", "-w", "3840", str(OUT / "lockup.svg"), "-o", str(OUT / "video/lockup-3840.png")], check=True
    )
    word, w = wordmark(FG_ON_INK, 120, 0, 0)
    total = 64 * 4 + 72 + w
    x0 = (1920 - total) / 2
    card = (
        f'<rect width="1920" height="1080" fill="{INK}"/>'
        f'<g transform="translate({x0:.1f} 412) scale(4)">{on_ink}</g>'
        f'<g transform="translate({x0 + 64 * 4 + 72:.1f} 592)">{word}</g>'
    )
    card_svg = write("video/endcard.svg", svg("0 0 1920 1080", card, (1920, 1080)))
    png(card_svg, OUT / "video/endcard.png", 1920, 1080)


if __name__ == "__main__":
    main()
