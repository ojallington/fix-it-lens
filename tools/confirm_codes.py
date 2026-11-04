"""A person confirms the fault codes a card reads from the manual's drawings (not text-checked), and accepts each
step we added ourselves (origin fixit), one by one:

    python -m tools.confirm_codes <card.yaml>

It shows each page that draws the codes (a PNG under FIXIT_PRIVATE/review/, opened when it can be), asks for your
name, then asks for each code whether the page shows it beside that meaning. A yes is written to the card's check
record as confirmed_by and confirmed_at; a no is written as rejected_by, and the card is never shared with it. Each
of our own steps ("Plug the appliance back in.") is shown after the job's last step from the manual, and a yes is
written as accepted_by and accepted_at. Only a person at a terminal can run it: a model's eye check does not count,
so it refuses without one.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import calls
from ingest import check_pr, flow, pages
from ingest.cards import card_path, read_card


def page_png(card: dict[str, object], household: str | None, page: int) -> Path:
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    brand, model = str(card["brand"]), str(card["model"])
    pdf = pages.manual_path(brand, model, household)
    if not pdf.exists():
        pdf = pages.manual_path(brand, model)
    priv = calls.private_dir()
    assert priv is not None, "FIXIT_PRIVATE is needed: the page images are the maker's"
    out = priv / "review" / f"{brand}-{card_path(brand, model).stem}-p{page}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf))
    try:
        doc[page - 1].render(scale=150 / 72).to_pil().save(out)
    finally:
        doc.close()
    return out


def _en(text: dict[str, str]) -> str:
    return text.get("en") or next(iter(text.values()), "?")


def placed(card: dict[str, Any], job: str, step: str) -> str:
    """Where the card puts our step: before the manual's next step when one follows (plugging in before the step that
    runs the machine), else at the end of the job."""
    steps = next((j["steps"] for j in card.get("jobs", []) if j["id"] == job), [])
    at = next((i for i, s in enumerate(steps) if s["id"] == step), len(steps))
    nxt = next((s for s in steps[at + 1:] if s.get("origin") != "fixit"), None)
    return f"just before the manual's step \"{_en(nxt['text'])}\"" if nxt else "at the end"


def confirm(card_file: Path, ask: Callable[[str], str] = input, show: bool = True) -> int:
    card = read_card(card_file)
    rec_file = check_pr.record_path(card_file)
    rec = json.loads(rec_file.read_text()) if rec_file.exists() else {}
    if rec.get("card_sha256") != check_pr.card_sha256(card_file):
        print("The check record is for another version of this card: run make check-pr first.")
        return 1
    todo = [d for d in rec.get("drawn_codes", []) if not d.get("confirmed_by") and not d.get("rejected_by")]
    ours = [d for d in rec.get("fixit_steps", []) if not d.get("accepted_by") and not d.get("rejected_by")]
    if not todo and not ours:
        print("Nothing to confirm: every drawn code and every step of ours has an answer.")
        return 0
    meaning = {r["id"]: r["meaning"].get("en") or next(iter(r["meaning"].values()))
               for r in card.get("troubleshooting", [])}
    household = flow.household_of(card_file)
    for page in sorted({d["page"] for d in todo}):
        png = page_png(card, household, page)
        print(f"Page {page}: {png}")
        if show and shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(png)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    name = ""
    while not name:
        name = ask("Your name: ").strip()
    when = datetime.now(UTC).date().isoformat()
    for d in todo:
        said = ""
        while said not in ("y", "n"):
            said = ask(f"Page {d['page']}: is the code {d['code']} drawn beside \"{meaning.get(d['row'], '?')}\"? "
                       "[y/n] ").strip().lower()[:1]
        if said == "y":
            d["confirmed_by"], d["confirmed_at"] = name, when
        else:
            d["rejected_by"], d["confirmed_at"] = name, when
    steps = {(j["id"], s["id"]): s for j in card.get("jobs", []) for s in j["steps"]}
    for d in ours:
        before = steps.get((d["job"], d["restores"]), {}).get("text", {})
        print(f"Job {d['job']}: the manual's step \"{_en(before)}\" is never undone in the manual.")
        said = ""
        while said not in ("y", "n"):
            said = ask(f"Add our own step {placed(card, d['job'], d['step'])}, \"{d['text']}\"? [y/n] ").strip() \
                .lower()[:1]
        if said == "y":
            d["accepted_by"], d["accepted_at"] = name, when
        else:
            d["rejected_by"], d["accepted_at"] = name, when
    rec_file.write_text(json.dumps(rec, indent=1) + "\n")
    left = check_pr.unconfirmed(rec) + check_pr.unaccepted(rec)
    print("All confirmed." if not left else f"Not confirmed: {', '.join(left)}. The card stays private.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.confirm_codes", description=__doc__.splitlines()[0])
    ap.add_argument("card", type=Path)
    a = ap.parse_args(argv)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("A person confirms these codes at a terminal; this needs one.", file=sys.stderr)
        return 2
    return confirm(a.card)


if __name__ == "__main__":
    sys.exit(main())
