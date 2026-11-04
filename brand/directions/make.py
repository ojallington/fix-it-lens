"""Draws the four mark directions as SVG: the mark, the lockup with the wordmark, the maskable app icon, and the mark
at 16, 32, 64 and 256 px. Run it from anywhere; it writes next to itself.

Every mark is drawn on a 64-unit grid. The 16 px file is its own drawing on a 16-unit grid, snapped to whole pixels,
because the 64-unit drawing loses its detail at that size. Colours are the app's tokens (phone/src/style.css).
"""

from pathlib import Path

INK = "#15130f"  # --bg, dark
PAPER = "#f1ece0"  # --fg, dark
FAINT = "#958e7e"  # --faint, dark
ACCENT = "#93a8ff"  # --blue, dark
ACCENT_LIGHT = "#2a49d8"  # --blue, light
INK_LIGHT = "#1c1a16"  # --fg, light

OUT = Path(__file__).parent


# Each direction gives: name, rationale, the 64-grid mark body (fg = line colour, acc = accent), and the 16-grid body.


def lockon(fg, acc, bg):
    # Viewfinder corners around the part, outlined the way the camera outlines it.
    corners = (
        f'<path d="M6 20V6h14M44 6h14v14M58 44v14H44M20 58H6V44" fill="none" stroke="{fg}" stroke-width="5" '
        f'stroke-linecap="square"/>'
    )
    part = (
        f'<path d="M24 22c5-4 13-4 17 0 4 3 3 8 6 11 3 4 0 10-5 11-6 2-10-1-15 0-5 1-9-3-8-8 1-4-1-6 0-9 1-2 3-4 5-5z" '
        f'fill="{acc}" fill-opacity="0.4" stroke="{acc}" stroke-width="4" stroke-linejoin="round"/>'
    )
    return corners + part


def lockon16(fg, acc, bg):
    return (
        f'<path d="M1.5 5.5v-4h4M10.5 1.5h4v4M14.5 10.5v4h-4M5.5 14.5h-4v-4" fill="none" stroke="{fg}" '
        f'stroke-width="1.5" stroke-linecap="square"/>'
        f'<path d="M6 5h3.5l1.5 2v3l-1.5 1H6l-1-1.5V6.5z" fill="{acc}"/>'
    )


def page(fg, acc, bg):
    # The maker's manual: text above, the exploded diagram below, and the named part outlined on the page itself.
    return (
        f'<path d="M10 6h30l14 14v38H10z" fill="none" stroke="{fg}" stroke-width="4.5" stroke-linejoin="round"/>'
        f'<path d="M40 6v14h14" fill="none" stroke="{fg}" stroke-width="4.5" stroke-linejoin="round"/>'
        f'<path d="M18 17h14M18 26h28" stroke="{fg}" stroke-width="4" stroke-linecap="round"/>'
        f'<path d="M19 43h9" stroke="{fg}" stroke-width="3"/><circle cx="17" cy="43" r="3.5" fill="{fg}"/>'
        f'<path d="M30 36c4-3 12-3 15 1 3 3 2 9-2 12-4 2-10 2-13-1-3-3-3-9 0-12z" fill="{acc}" fill-opacity="0.4" '
        f'stroke="{acc}" stroke-width="4" stroke-linejoin="round"/>'
    )


def page16(fg, acc, bg):
    return (
        f'<path d="M2.5 1.5h7l3.5 3.5v9.5h-10.5z" fill="none" stroke="{fg}" stroke-width="1.25" '
        f'stroke-linejoin="round"/>'
        f'<path d="M4.5 5h4" stroke="{fg}" stroke-width="1.25"/>'
        f'<path d="M5 8.5h5.5l1 1.5v2l-1 1H5.5L4.5 12v-2.5z" fill="{acc}"/>'
    )


def step(fg, acc, bg):
    # A manual's numbered steps: two checked, the one in hand marked in the accent.
    return (
        f'<path d="M8 13l4 4 7-8" fill="none" stroke="{fg}" stroke-width="4.5" stroke-linecap="round" '
        f'stroke-linejoin="round"/>'
        f'<path d="M27 13h29" stroke="{fg}" stroke-width="4.5" stroke-linecap="round"/>'
        f'<path d="M8 32l4 4 7-8" fill="none" stroke="{fg}" stroke-width="4.5" stroke-linecap="round" '
        f'stroke-linejoin="round"/>'
        f'<path d="M27 32h21" stroke="{fg}" stroke-width="4.5" stroke-linecap="round"/>'
        f'<rect x="6" y="44" width="52" height="16" rx="3" fill="{acc}"/>'
        f'<path d="M12 52h8" stroke="{bg}" stroke-width="4.5" stroke-linecap="round"/>'
        f'<path d="M27 52h24" stroke="{bg}" stroke-width="4.5" stroke-linecap="round"/>'
    )


def step16(fg, acc, bg):
    return (
        f'<path d="M1.5 3.5l1.5 1.5 2.5-3M1.5 8l1.5 1.5 2.5-3" fill="none" stroke="{fg}" stroke-width="1.25"/>'
        f'<path d="M7 3.5h8M7 8h6" stroke="{fg}" stroke-width="1.5"/>'
        f'<rect x="1" y="11" width="14" height="5" rx="1" fill="{acc}"/>'
    )


def ring(fg, acc, bg):
    # Today's ring, kept; the dot inside becomes the part the camera outlines, with its handles.
    pts = [(22, 27), (36, 21), (44, 33), (35, 43), (24, 40)]
    d = "M" + "L".join(f"{x} {y}" for x, y in pts) + "Z"
    handles = "".join(f'<rect x="{x - 2.5}" y="{y - 2.5}" width="5" height="5" fill="{fg}"/>' for x, y in pts[::2])
    return (
        f'<circle cx="32" cy="32" r="26" fill="none" stroke="{acc}" stroke-width="6"/>'
        f'<path d="{d}" fill="{acc}" fill-opacity="0.35" stroke="{fg}" stroke-width="3" stroke-linejoin="round"/>'
        + handles
    )


def ring16(fg, acc, bg):
    return (
        f'<circle cx="8" cy="8" r="6.25" fill="none" stroke="{acc}" stroke-width="1.75"/>'
        f'<path d="M5 6.5l4-1.5 2 3-2 2.5-3-.5z" fill="{fg}"/>'
    )


DIRECTIONS = [
    (
        "a-lockon",
        "Lock-on",
        "The camera's corners close on the part and outline it: what the product does in the first second, drawn.",
        lockon,
        lockon16,
    ),
    (
        "b-page",
        "The page, outlined",
        "The maker's manual with the named part outlined in its diagram: "
        "it reads your manual and finds the part for you.",
        page,
        page16,
    ),
    (
        "c-step",
        "Step in hand",
        "A field manual's steps, two ticked and the one in hand lit: the product checks each step before the next.",
        step,
        step16,
    ),
    (
        "d-ring",
        "Ring, outlined",
        "Today's ring kept; its dot becomes the outlined part with its handles, so the mark grows from what exists.",
        ring,
        ring16,
    ),
]


def svg(view, body, size=None, title=None):
    wh = f' width="{size}" height="{size}"' if size else ""
    t = f"<title>{title}</title>" if title else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}"{wh}>{t}{body}</svg>\n'


def tile(body, bg, inset=0.0):
    """The mark on a 64-unit square tile; inset shrinks it toward the centre."""
    s = 1 - 2 * inset
    return (
        f'<rect width="64" height="64" fill="{bg}"/>'
        f'<g transform="translate({64 * inset} {64 * inset}) scale({s})">{body}</g>'
    )


def lockup(body, fg, bg, word_fg):
    # Mark at 64, wordmark in Fraunces beside it, cap height matched to the mark's inner frame.
    return (
        f'<rect width="360" height="64" fill="{bg}"/>{body}'
        f'<text x="84" y="45" font-family="Fraunces, Georgia, serif" font-size="38" font-weight="600" '
        f'letter-spacing="-0.6" fill="{word_fg}">Fix-It Lens</text>'
    )


def main():
    for slug, name, rationale, draw, draw16 in DIRECTIONS:
        d = OUT / slug
        d.mkdir(exist_ok=True)
        dark = draw(PAPER, ACCENT, INK)
        light = draw(INK_LIGHT, ACCENT_LIGHT, "#f2ede2")
        (d / "mark.svg").write_text(svg("0 0 64 64", dark, title=f"Fix-It Lens: {name}"))
        (d / "mark-light.svg").write_text(svg("0 0 64 64", light, title=f"Fix-It Lens: {name}"))
        (d / "lockup.svg").write_text(svg("0 0 360 64", lockup(dark, PAPER, INK, PAPER)))
        (d / "lockup-light.svg").write_text(svg("0 0 360 64", lockup(light, INK_LIGHT, "#f2ede2", INK_LIGHT)))
        # Maskable: Android may crop to a circle of 80% diameter, so the mark sits in the middle 60%.
        (d / "icon-maskable.svg").write_text(svg("0 0 64 64", tile(dark, INK, 0.2), size=512))
        (d / "icon.svg").write_text(
            svg(
                "0 0 64 64",
                f'<rect width="64" height="64" rx="14" fill="{INK}"/>'
                f'<g transform="translate(8 8) scale(0.75)">{dark}</g>',
                size=512,
            )
        )
        (d / "mark-16.svg").write_text(
            svg("0 0 16 16", f'<rect width="16" height="16" rx="3" fill="{INK}"/>{draw16(PAPER, ACCENT, INK)}', size=16)
        )
        for px in (32, 64, 256):
            (d / f"mark-{px}.svg").write_text(
                svg(
                    "0 0 64 64",
                    f'<rect width="64" height="64" rx="10" fill="{INK}"/>'
                    f'<g transform="translate(4 4) scale(0.875)">{dark}</g>',
                    size=px,
                )
            )
        (d / "README.md").write_text(f"# {name}\n\n{rationale}\n")


if __name__ == "__main__":
    main()
