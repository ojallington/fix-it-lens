"""fixit ingest --pr: a contributor's manual becomes a card pull request.

After the card is built, `make check-pr`'s local check runs on the same PDF (page hashes, every quote) and writes the
record; a card that fails it is never proposed. Then, in the card's checkout: a branch `card/<brand>-<model>`, a commit
of exactly the card and its record (staged by path), a push, and `gh pr create`. The PR body carries the rail line,
counts, each rejection's place and reason, and each job the loader's rules check refuses (run as
`python -m tools.check_cards`, since ingest/ imports neither app/ nor rules/); never manual text, never the PDF.
A card the rules check refuses as a whole is never proposed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ingest import appliance_file, check_pr

Run = Callable[..., subprocess.CompletedProcess[str]]
HOME = Path(__file__).resolve().parents[1]  # the Fix-It Lens checkout, where tools/ and app/ live


class PrError(Exception):
    pass


def branch_name(brand: str, model: str) -> str:
    return "card/" + re.sub(r"[^a-z0-9-]+", "-", f"{brand}-{model}".lower()).strip("-")


def rules_check(card_file: Path, country: str, run: Run) -> dict[str, Any]:
    """The loader's rules check on the card: {status, failures (card-wide), refused: {job: [reasons]}}."""
    out = run([sys.executable, "-m", "tools.check_cards", "--json", "--country", country, str(card_file.resolve())],
              cwd=HOME, capture_output=True, text=True)
    try:
        return dict(json.loads(out.stdout)[0])
    except (ValueError, IndexError, KeyError) as e:
        raise PrError(f"the rules check did not run: {out.stderr.strip()[:300]}") from e


def pr_body(card: dict[str, Any], res: appliance_file.IngestResult, record: dict[str, Any],
            rules: dict[str, Any] | None = None) -> str:
    m = card["manual"]
    steps = sum(len(j["steps"]) for j in card["jobs"])
    lines = [
        f"**{card['brand'].title()} {card['model']}** ({card['appliance_kind']}): {res.rail}",
        "",
        f"- manual: {m['source_url']} ({m['page_count']} pages, lang {m['lang']}, pdf sha256 `{record['pdf_sha256']}`)",
        f"- jobs: {', '.join(j['id'] for j in card['jobs'])}",
        f"- {steps} steps, {len(card['red_zones'])} red zones, "
        f"{sum(1 for j in card['jobs'] if j['interval'])} intervals; built by {card['appliance_file_model']}",
        f"- `make check-pr` on the contributor's PDF: {len(record['quotes'])} quotes, all passed; "
        f"record `{card['model']}.check.json` (hashes only)",
    ]
    if res.rejected:
        lines += ["", "Rejected by the citation check (dropped from the card):"]
        lines += [f"- `{r.where}` p{r.page}: {r.reason.split(':')[0]} → dropped {r.dropped}" for r in res.rejected]
    if res.corrected:
        lines += ["", "Page corrections: " + ", ".join(f"`{w}` p{a}→p{b}" for w, a, b in res.corrected)]
    refused = (rules or {}).get("refused") or {}
    if refused:
        lines += ["", f"Refused by the rules check ({rules['country'] if rules else ''}): these jobs stay in the "
                  "card file but leave it at load, so no session offers them:"]
        lines += [f"- `{jid}`: {why}" for jid, whys in refused.items() for why in whys]
    lines += ["", "No manual text is in this PR beyond the cited quotes (at most two sentences each)."]
    return "\n".join(lines) + "\n"


def _git(run: Run, repo: Path, *args: str) -> str:
    out = run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise PrError(f"git {' '.join(args)}: {out.stderr.strip()[:300]}")
    return str(out.stdout).strip()


def open_pr(card_file: Path, pdf: Path, res: appliance_file.IngestResult, *, push: bool = True,
            country: str = "nl", run: Run = subprocess.run) -> str:
    """Check, branch, commit, push and open the PR; returns the PR URL (or the branch when push is False)."""
    assert res.card is not None
    errs, record = check_pr.check_local(card_file, pdf)
    if errs:
        raise PrError("the card fails make check-pr on this PDF: " + "; ".join(errs[:5]))
    rec_file = check_pr.record_path(card_file)
    rules = rules_check(card_file, country, run)
    if rules["status"] != "pass":
        raise PrError("the card fails the rules check: " + "; ".join(rules["failures"][:5]))
    rec_file.write_text(json.dumps(record, indent=1) + "\n")
    repo = Path(_git(run, card_file.parent, "rev-parse", "--show-toplevel"))
    paths: Sequence[str] = [str(card_file.relative_to(repo)), str(rec_file.relative_to(repo))]
    branch = branch_name(res.card["brand"], res.card["model"])
    _git(run, repo, "switch", "-c", branch)
    _git(run, repo, "add", "--", *paths)
    title = f"Card: {res.card['brand'].title()} {res.card['model']}"
    _git(run, repo, "commit", "-m", f"{title}\n\n{res.rail}", "--", *paths)
    if not push:
        return branch
    _git(run, repo, "push", "-u", "origin", branch)
    out = run(["gh", "pr", "create", "--title", title, "--body-file", "-", "--head", branch], cwd=repo,
              input=pr_body(res.card, res, record, rules), capture_output=True, text=True)
    if out.returncode != 0:
        raise PrError(f"gh pr create: {out.stderr.strip()[:300]}")
    return str(out.stdout).strip()
