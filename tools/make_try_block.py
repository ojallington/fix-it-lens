"""Write the README's top "Try it" block, and its QR codes, from docs/readme/try.toml.

    uv run python tools/make_try_block.py

The block sits between the two `try` comments near the top of README.md. With `open = true` it links the app's door
and shows its QR: two SVGs, one per theme, picked by a <picture>. Both keep dark modules on a light tile, the polarity
every phone camera reads; the dark-theme tile is a softer white so it does not glare on a dark page. With
`open = false` the QR files are removed and the block names only the film, or nothing until `video` is set.
tests/l0/test_readme.py rebuilds both, checks the files and the README match, and decodes the code.
"""

from __future__ import annotations

import io
import re
import sys
import tomllib
from pathlib import Path

import segno

ROOT = Path(__file__).resolve().parents[1]
SETTING = ROOT / "docs/readme/try.toml"
README = ROOT / "README.md"
OUT = ROOT / "docs/readme"
THEMES = {"light": ("#1f2328", "#ffffff"), "dark": ("#0d1117", "#f0f6fc")}
SCALE, BORDER = 6, 4
START = "<!-- try: written by tools/make_try_block.py from docs/readme/try.toml -->"
END = "<!-- /try -->"
SPAN = re.compile(re.escape(START) + r"\n.*?" + re.escape(END), re.DOTALL)


def setting() -> dict:
    s = tomllib.loads(SETTING.read_text())
    s.setdefault("repo_public", False)    # a missing key is a private repo
    for key in ("open", "repo_public"):
        if not isinstance(s.get(key), bool):
            raise SystemExit(f"{SETTING.relative_to(ROOT)}: {key} must be true or false")
    return s


def qr(value: str) -> segno.QRCode:
    return segno.make_qr(value, error="m")


def svg(value: str, theme: str) -> str:
    dark, light = THEMES[theme]
    buf = io.BytesIO()
    qr(value).save(buf, kind="svg", scale=SCALE, border=BORDER, dark=dark, light=light, xmldecl=False,
                   svgclass=None, lineclass=None, title="Fix-It Lens on your phone")
    return buf.getvalue().decode() + "\n"


def block(s: dict) -> str:
    """The README's block for this setting, markers included."""
    if s["open"]:
        url = s["url"]
        body = f"""<a href="{url}"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/try-qr-dark.svg">
  <img src="docs/readme/try-qr-light.svg" width="164" height="164" align="right" alt="QR code for {url}">
</picture></a>

**Try it on your phone:** scan the code, or open [{url.removeprefix("https://")}]({url}). It runs
in the phone's browser, with nothing to install: open it and point your phone. If the public door is closed, it
plays a recorded repair instead."""
    elif s.get("video"):
        body = f"**Watch it work:** [the film]({s['video']})."
    else:
        return f"{START}\n{END}"
    return f"{START}\n{body}\n{END}"


def write(s: dict) -> list[str]:
    """Rewrite the block and write (open) or remove (shut) the QR files; returns what changed."""
    text = README.read_text()
    if len(SPAN.findall(text)) != 1:
        raise SystemExit("README.md needs exactly one try block between its two `try` comments")
    done = []
    new = SPAN.sub(lambda _: block(s), text)
    if new != text:
        README.write_text(new)
        done.append("README.md")
    for theme in THEMES:
        path = OUT / f"try-qr-{theme}.svg"
        if s["open"]:
            path.write_text(svg(s["url"], theme))
            done.append(str(path.relative_to(ROOT)))
        elif path.exists():
            path.unlink()
            done.append(f"removed {path.relative_to(ROOT)}")
    return done


def main() -> int:
    s = setting()
    done = write(s)
    way = f"open: {s['url']}" if s["open"] else f"shut: {s.get('video') or 'no film yet'}"
    print(f"try block {way}; " + (", ".join(done) or "no change"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
