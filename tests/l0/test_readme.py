"""The README's model table is true of the code: each row's model is the one config/models.toml pins for its roles,
every role has a row, every file it links exists, and the repo files a judge clicks first are there."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()
MODELS = tomllib.loads((ROOT / "config/models.toml").read_text())
PLANNED_REMOTE = "https://github.com/ojallington/fix-it-lens"


def _section(title: str) -> str:
    start = README.index(title)
    return README[start:README.index("\n## ", start + 1)]


def _rows() -> list[tuple[str, list[str]]]:
    """The model table's rows, each with the phase row above it (a bold first cell)."""
    out, phase = [], ""
    for ln in _section("## How it works").splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if not ln.startswith("| ") or len(cells) != 3:
            continue
        if cells[0].startswith("**") and not cells[1]:
            phase = cells[0].strip("*")
        elif "`" in cells[0] and "<br/>" in cells[0]:
            out.append((phase, cells))
    return out


def _pinned(role: str, local: bool) -> str:
    api = MODELS["roles"][role]["model"]
    return MODELS["local"]["roles"].get(role, {}).get("model", api) if local else api


def test_each_row_names_the_pinned_model() -> None:
    rows = _rows()
    assert len(rows) >= 9
    covered: set[tuple[str, bool]] = set()
    assert {phase for phase, _ in rows} == {"Before the job", "During the job", "Is the step done?", "No connection"}
    for phase, (model, _does, decides) in rows:
        local = phase == "No connection"
        ids = re.findall(r"`([^`]+)`", model)
        model_id, roles = ids[0], ids[1:]
        assert roles, model
        for role in roles:
            assert _pinned(role, local) == model_id, (model, role)
            covered.add((role, local))
        for link in re.findall(r"\]\(([^)]+)\)", decides):
            assert (ROOT / link).is_file(), link
    assert {(r, False) for r in MODELS["roles"]} <= covered
    assert {(r, True) for r in MODELS["local"]["roles"] if not r.startswith("sam")} <= covered


def test_the_diagram_draws_every_model() -> None:
    fence = re.search(r"```mermaid\n(.*?)```", _section("## How it works"), re.S)
    assert fence
    for name in ("Muse Spark", "Muse Wonder", "SAM 3.1", "Muse Voice", "Muse Glimmer", "app/verdict.py"):
        assert name in fence.group(1), name


def test_the_claims_behind_the_rows_are_where_the_readme_says() -> None:
    assert "def combine(" in (ROOT / "app/verdict.py").read_text()
    assert "no path from the model to a clear" in (ROOT / "rules/engine.py").read_text()
    assert "four frames" in (ROOT / "app/judge.py").read_text()
    assert "after 2 consecutive API failures" in (ROOT / "calls/breaker.py").read_text()
    assert "SAM 3.1 masks from that box" in (ROOT / "app/locate.py").read_text()
    assert "The serial is never asked for" in (ROOT / "ingest/plate.py").read_text()


@pytest.mark.parametrize("name", ["LICENSE", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md",
                                  ".github/CODEOWNERS"])
def test_repo_files_a_judge_opens(name: str) -> None:
    assert (ROOT / name).is_file()


def test_contributing_commands_exist() -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text()
    cli = (ROOT / "ingest/cli.py").read_text()
    assert "fixit ingest build" in text and "ingest build" in cli and "--pr" in cli and "check-pr" in cli
    make = (ROOT / "Makefile").read_text()
    for target in ("hooks", "ci", "eval", "claims"):
        assert re.search(rf"^{target}:", make, re.M), target


def test_clone_line_is_the_remote() -> None:
    """The clone line names the planned GitHub URL until the repo has a remote; then it must be that remote."""
    assert f"git clone {PLANNED_REMOTE} " in README
    assert "<this repo>" not in README
    got = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"], capture_output=True, text=True)
    if got.returncode != 0:
        pytest.xfail(f"no remote yet: the README's clone line is the planned {PLANNED_REMOTE}")
    assert got.stdout.strip().removesuffix(".git") in (PLANNED_REMOTE, PLANNED_REMOTE.replace("https://", "git@")
                                                       .replace("github.com/", "github.com:"))


def _modules(svg: str) -> list[tuple[int, int, int]]:
    """The dark runs drawn by segno's one stroked path: (row, first column, length), read from its M/m/h commands."""
    d = re.search(r'<path stroke="[^"]+" d="([^"]+)"', svg)
    assert d, "no module path in the QR"
    x = y = 0.0
    runs = []
    for op, a, b in re.findall(r"([Mmh])(-?[\d.]+)(?:[ ,](-?[\d.]+))?", d.group(1)):
        if op == "M":
            x, y = float(a), float(b)
        elif op == "m":
            x, y = x + float(a), y + float(b)
        else:
            runs.append((int(y - 0.5), int(x), int(float(a))))
            x += float(a)
    return runs


def _decodes(svg: str, scale: int) -> str:
    import cv2
    import numpy as np

    size = int(re.search(r'width="(\d+)"', svg).group(1)) // scale
    img = np.full((size, size), 255, np.uint8)
    for row, col, n in _modules(svg):
        img[row, col:col + n] = 0
    big = cv2.resize(img, (size * 8, size * 8), interpolation=cv2.INTER_NEAREST)
    return cv2.QRCodeDetector().detectAndDecode(big)[0]


def test_the_try_block_is_the_one_setting() -> None:
    """README's top block and the QR files are what docs/readme/try.toml says, whichever way its switch is set."""
    from tools import make_try_block as t

    s = t.setting()
    assert t.SPAN.findall(README) == [t.block(s)], "run: uv run python tools/make_try_block.py"
    head = README[:README.index("## Try it now")]
    for theme in t.THEMES:
        path = ROOT / f"docs/readme/try-qr-{theme}.svg"
        if s["open"]:
            assert path.read_text() == t.svg(s["url"], theme), "run: uv run python tools/make_try_block.py"
            assert _decodes(path.read_text(), t.SCALE) == s["url"]
        else:
            assert not path.exists(), "a shut door shows no QR: run uv run python tools/make_try_block.py"
    if not s["open"]:
        # no way in that does not answer: no try link, no QR, no hosted page above the fold; only the film, once set
        assert "<picture>" not in head and "try-qr" not in head
        assert urlparse(s["url"]).netloc not in head and urlparse(s["watch"]).netloc not in head
        assert not s["video"] or f"]({s['video']})" in head


def test_a_shut_door_names_only_the_film_and_nothing_before_it_has_one() -> None:
    from tools import make_try_block as t

    s = {**t.setting(), "open": False, "video": ""}
    assert t.block(s) == f"{t.START}\n{t.END}"
    film = t.block({**s, "video": "https://film.example/x"})
    assert "](https://film.example/x)" in film and film.count("](") == 1


def test_the_try_switches_are_booleans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """open and repo_public are true or false; repo_public may be left out (false), open may not."""
    from tools import make_try_block as t

    assert isinstance(t.setting()["repo_public"], bool)
    for bad in ('open = "no"\n', "repo_public = 0\nopen = false\n", 'repo_public = false\n'):
        (tmp_path / "try.toml").write_text(bad)
        monkeypatch.setattr(t, "SETTING", tmp_path / "try.toml")
        monkeypatch.setattr(t, "ROOT", tmp_path)
        with pytest.raises(SystemExit):
            t.setting()
    (tmp_path / "try.toml").write_text("open = false\n")
    assert t.setting()["repo_public"] is False


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_open_block_shows_the_qr_for_the_url_and_decodes_to_it(theme: str) -> None:
    """The other side of the switch, so flipping it on the owner's go is one edit and one run."""
    from tools import make_try_block as t

    s = {**t.setting(), "open": True}
    url = s["url"]
    assert _decodes(t.svg(url, theme), t.SCALE) == url
    head = t.block(s)
    assert f'<a href="{url}">' in head and 'srcset="docs/readme/try-qr-dark.svg"' in head
    assert f'alt="QR code for {url}"' in head
    assert f"]({url})" in head and url.removeprefix("https://") in head    # the URL as text, for a desktop reader


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_designed_figure_is_the_layout_and_stands_alone(theme: str) -> None:
    from html import unescape

    from tools import make_readme_diagram as fig

    svg = (ROOT / f"docs/readme/how-it-works-{theme}.svg").read_text()
    drawn = [unescape(t) for t in re.findall(r"<text [^>]*>([^<]*)</text>", svg)]
    assert sorted(drawn) == sorted(t for _, t in fig.texts() if t), "run: tools/make_readme_diagram.py --fonts …"
    assert all(c in svg for c in fig.PALETTES[theme].values())
    # nothing from outside: fonts are data: URIs, and no href, image or import
    assert re.findall(r"url\(([^)]*)\)", svg) and all(u.startswith(("data:font/woff2", "#")) for u in
                                                      re.findall(r"url\(([^)]*)\)", svg))
    assert not re.search(r"<image|xlink:href|@import|https?://(?!www\.w3\.org/2000/svg)", svg)
    for mark in [b.mark for b in fig.BOXES] + [fig.GLIMMER.mark]:
        path = mark.split(" · ")[0].lower()
        if "/" in path:
            assert (ROOT / path).exists(), path
    models = {fig.PLATE.title, fig.WONDER.title, fig.SAM.title, fig.VOICE.title, fig.GLIMMER.title}
    assert {"Muse Spark", "Muse Wonder", "SAM 3.1", "Muse Voice"} <= models
    assert {o for _, o in fig.OUTCOMES} == {"DONE", "NOT DONE", "CAN'T SEE: MOVE CLOSER"}
    assert 'Result = Literal["done", "not_done", "cant_see"]' in (ROOT / "app/verdict.py").read_text()
    head = _section("## How it works")
    assert 'srcset="docs/readme/how-it-works-dark.svg"' in head and 'src="docs/readme/how-it-works-light.svg"' in head
    assert "<details><summary>" in head and "```mermaid" in head


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_five_models_figure_is_the_layout_and_the_pinned_ids(theme: str) -> None:
    from html import unescape

    from tools import make_readme_models as fig

    svg = (ROOT / f"docs/readme/five-models-{theme}.svg").read_text()
    drawn = [unescape(t) for t in re.findall(r"<text [^>]*>([^<]*)</text>", svg)]
    assert sorted(drawn) == sorted(t for _, t in fig.texts() if t), "run: tools/make_readme_models.py --fonts …"
    assert re.findall(r"url\(([^)]*)\)", svg) and all(u.startswith(("data:font/woff2", "#")) for u in
                                                      re.findall(r"url\(([^)]*)\)", svg))
    assert not re.search(r"<image|xlink:href|@import|https?://(?!www\.w3\.org/2000/svg)", svg)
    assert [m.title for m in fig.MODELS] == ["Muse Wonder", "Muse Spark", "SAM 3.1", "Muse Voice", "Muse Glimmer"]
    for m in fig.MODELS:
        for role in m.roles:
            assert _pinned(role, m.local) == m.model_id, (m.title, role)
    assert {r for m in fig.MODELS if not m.local for r in m.roles} == set(MODELS["roles"])
    for mark in (fig.PHONE.mark, fig.SERVER.mark):
        for path in mark.lower().split(" · "):
            if path.endswith("/"):
                assert (ROOT / path).is_dir(), path
    assert '@app.websocket("/ws")' in (ROOT / "app/server.py").read_text()
    head = _section("## How it works")
    assert 'srcset="docs/readme/five-models-dark.svg"' in head and 'src="docs/readme/five-models-light.svg"' in head
