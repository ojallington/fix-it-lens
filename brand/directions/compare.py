"""Builds compare.html: the four directions side by side. Run make.py first.
Needs rsvg-convert for the true-pixel 16 and 32 px renders."""

import base64
import re
import subprocess
from pathlib import Path

from make import ACCENT, DIRECTIONS, INK, PAPER

HERE = Path(__file__).parent

TODAY = (
    f'<rect width="64" height="64" rx="10" fill="{INK}"/><circle cx="32" cy="32" r="19" fill="none" '
    f'stroke="{ACCENT}" stroke-width="5.5"/><circle cx="32" cy="32" r="7" fill="{PAPER}"/>'
)


def inline(path, cls=""):
    s = Path(path).read_text()
    s = re.sub(r"<title>.*?</title>", "", s)
    s = re.sub(r'(<svg[^>]*?) width="\d+" height="\d+"', r"\1", s, count=1)
    return s.replace("<svg ", f'<svg class="{cls}" aria-hidden="true" ', 1).strip()


def png(svg_text, px):
    out = subprocess.run(
        ["rsvg-convert", "-w", str(px), "-h", str(px)], input=svg_text.encode(), capture_output=True, check=True
    )
    return "data:image/png;base64," + base64.b64encode(out.stdout).decode()


def raster(path, px):
    return png(Path(path).read_text(), px)


def tab(src, label):
    return f'<div class="tab"><img src="{src}" width="16" height="16" alt=""><span>{label}</span></div>'


def section(i, slug, name, rationale):
    d = HERE / slug
    p16, p32 = raster(d / "mark-16.svg", 16), raster(d / "mark-32.svg", 32)
    icon = inline(d / "icon.svg", "appicon")
    blank = [
        f'<div class="app"><div class="blank b{k % 4}"></div><span>{t}</span></div>'
        for k, t in enumerate(
            [
                "Clock",
                "Notes",
                "Camera",
                "Maps",
                "Files",
                "Music",
                "Weather",
                "Photos",
                "Mail",
                "Calendar",
                "Settings",
                "Wallet",
                "Books",
                "Radio",
                "Home",
            ]
        )
    ]
    apps = "".join(blank[:5]) + f'<div class="app">{icon}<span>Fix-It Lens</span></div>' + "".join(blank[5:])
    return f"""
<section class="dir" id="{slug}">
  <header class="dir-head">
    <p class="kicker">Direction {"ABCD"[i]}</p>
    <h2>{name}</h2>
    <p class="why">{rationale}</p>
  </header>

  <div class="grid">
    <figure class="cell hero">{inline(d / "mark.svg", "big")}<figcaption>Mark, vector</figcaption></figure>

    <figure class="cell sizes">
      <div class="ladder">
        <div><img src="{p16}" width="16" height="16" alt="16 px"><span>16</span></div>
        <div><img src="{p32}" width="32" height="32" alt="32 px"><span>32</span></div>
        <div>{inline(d / "mark-64.svg", "s64")}<span>64</span></div>
        <div>{inline(d / "mark-256.svg", "s256")}<span>256</span></div>
      </div>
      <div class="zoom"><img src="{p16}" alt="16 px, enlarged"><img src="{p32}" alt="32 px, enlarged">\
<span>16 and 32 px rasters, enlarged pixel for pixel</span></div>
      <figcaption>Sizes: real pixels, then the same pixels enlarged. 16 is its own drawing.</figcaption>
    </figure>

    <figure class="cell lockups">
      {inline(d / "lockup.svg", "lockup")}
      {inline(d / "lockup-light.svg", "lockup")}
      <figcaption>With the wordmark, on charcoal and on paper</figcaption>
    </figure>

    <figure class="cell icons">
      <div class="icon-row">
        <div>{icon}<span>App icon</span></div>
        <div class="mask">{inline(d / "icon-maskable.svg", "appicon")}<span>Maskable, circle crop</span></div>
        <div class="mask sq">{inline(d / "icon-maskable.svg", "appicon")}<span>Maskable, squircle</span></div>
      </div>
      <figcaption>The maskable icon keeps the mark inside the 80% safe zone</figcaption>
    </figure>
  </div>

  <div class="mocks">
    <figure class="mock phone">
      <div class="screen">
        <div class="status"><span>9:41</span><span>●●● ▮</span></div>
        <div class="apps">{apps}</div>
      </div>
      <figcaption>Phone home screen</figcaption>
    </figure>

    <figure class="mock site">
      <div class="browser">
        <div class="tabs">{tab(p16, "Fix-It Lens")}<div class="tab off"><span>New tab</span></div></div>
        <div class="bar">
          {inline(d / "lockup.svg", "site-lockup")}
          <nav><span>How it works</span><span>Safety</span><span>Try the demo</span></nav>
        </div>
        <div class="page-body"><p class="kicker">Field manual / 01</p>\
<p class="headline">Upkeep before it breaks.</p></div>
      </div>
      <figcaption>Site header and browser tab</figcaption>
    </figure>

    <figure class="mock ha">
      <div class="ha-card">
        <div class="ha-top"><div class="ha-logo">{inline(d / "mark-256.svg", "ha-icon")}</div>
          <div><p class="ha-name">Fix-It Lens</p><p class="ha-sub">1 service · 6 entities</p></div></div>
        <div class="ha-row"><span>Filter clean due</span><b>in 4 days</b></div>
        <div class="ha-row"><span>Last job checked</span><b>Done</b></div>
      </div>
      <figcaption>Home Assistant integration tile</figcaption>
    </figure>
  </div>
</section>"""


def main():
    tabs = "".join(
        f'<div class="t16"><img src="{raster(HERE / s / "mark-16.svg", 16)}" width="16" height="16" alt="">'
        f'<img class="z" src="{raster(HERE / s / "mark-16.svg", 16)}" alt=""><span>{"ABCD"[i]} · {n}</span></div>'
        for i, (s, n, *_rest) in enumerate(DIRECTIONS)
    )
    today16 = png(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{TODAY}</svg>', 16)
    tabs = (
        f'<div class="t16 today"><img src="{today16}" width="16" height="16" alt="">'
        f'<img class="z" src="{today16}" alt=""><span>Today</span></div>' + tabs
    )
    body = "".join(section(i, s, n, r) for i, (s, n, r, *_rest) in enumerate(DIRECTIONS))
    html = (HERE / "compare.template.html").read_text().replace("{{STRIP}}", tabs).replace("{{SECTIONS}}", body)
    (HERE / "compare.html").write_text(html)


if __name__ == "__main__":
    main()
