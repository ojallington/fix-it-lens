"""The card loader. Every card line passes the citation check and the rule engine before a session may use it.
This is why only app/ imports rules.

- Rules: `rules.check_card` (rings, zones, the boiler allow-list, rule hits in step text), then each step's and
  done-when's words, in every language and as one whole line, through G0 (a look-only line passes only by its listed
  hash). A failure in the card's red zones fails the card; a failure or held line in a job refuses that job
  (it leaves card.jobs, its reason is in `refused` and the rules check's failures) and the other jobs stay usable.
- Troubleshooting rows (the manual's fault table): every text in a row through G0 as a step's is; a held row
  leaves the card with its reason (`rows_held`) and the card stays usable. A row's quote is in card.quotes.
- Citations: each step quote, red-zone quote and referenced quote is checked against the manual's page text, when the
  private folder has the manual. With no manual text, a repo card (under cards/) passes on its committed check
  record, cards/<brand>/<model>.check.json, written by `make check-pr` on the contributor's machine and checked in CI:
  the record's card digest must equal the card file's bytes and every quote in the card must be in it as passed
  (shown as "checked (record, CI-verified)"). A fresh install has only the repo card and must still get reminders.
  A missing or mismatched record is `unchecked`, and a session still refuses the card: unchecked is not passed.
- A card ingest built for a household (FIXIT_PRIVATE/cards) is cited against its manual in the private folder. The
  schedule's loader also asks for its check record, the same record bound to the card's bytes and to that manual's
  file digest (`private_record_failures`); a card with no record, an edited card or a replaced manual gives no
  reminders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

import rules
from app import config
from app.ports import Manuals, manual_household, pages_of
from contracts import load_card
from contracts.models.card import Card, CheckResult, Checks
from contracts.models.safety import Ok


@dataclass(frozen=True)
class Loaded:
    card: Card
    usable: bool
    failures: list[str]
    cited_by: Literal["manual", "record", "none"] = "none"  # what the citation check read
    refused: dict[str, str] = field(default_factory=dict)  # job id -> why the rules refused it (not in card.jobs)
    rows_held: dict[str, str] = field(default_factory=dict)  # troubleshooting row id -> why G0 held it (row left)
    needs_held: dict[str, str] = field(default_factory=dict)  # "<job>/<need>" -> why it left (G0, or its quote failed)

    @property
    def citation_label(self) -> str:
        status = self.card.checks.citation.status if self.card.checks else "unchecked"
        if status == "pass":
            return "checked (record, CI-verified)" if self.cited_by == "record" else "checked"
        return status


def record_failures(card_file: Path) -> list[str]:
    """Why the committed check record beside a repo card does not vouch for it ([] when it does)."""
    from ingest import check_pr  # the record's format and the CI check

    rec_file = check_pr.record_path(card_file)
    if not rec_file.exists():
        return [f"no check record ({rec_file.name})"]
    try:
        rec = json.loads(rec_file.read_text())
    except ValueError:
        return [f"{rec_file.name} is not JSON"]
    if rec.get("card_sha256") != hashlib.sha256(card_file.read_bytes()).hexdigest():
        return ["record: the card changed after its check (card digest differs)"]
    return [f"record: {e}" for e in check_pr.check_ci(card_file)]


_MANUAL_SHA: dict[tuple[str, int, int], str] = {}  # (path, mtime_ns, size) -> digest: /due loads every card


def _manual_sha256(manual: Path) -> str:
    from ingest import pages

    st = manual.stat()
    key = (str(manual), st.st_mtime_ns, st.st_size)
    if key not in _MANUAL_SHA:
        _MANUAL_SHA[key] = pages.file_sha256(manual)
    return _MANUAL_SHA[key]


def private_record_failures(card_file: Path) -> list[str]:
    """Why the check record beside a card ingest built (FIXIT_PRIVATE/cards) does not vouch for it ([] when it does):
    the record must vouch for the card as a repo card's does, and be bound to the manual in the private folder today
    (its pdf_sha256), so a card checked against another copy of the manual is not taken as checked."""
    from ingest import pages

    if fail := record_failures(card_file):
        return fail
    card = read(card_file)
    try:
        manual = pages.manual_path(card.brand, card.model, manual_household(card_file))
    except FileNotFoundError:
        return ["no private folder"]
    if not manual.exists():
        return [f"no manual for {card.brand} {card.model} in the private folder"]
    rec = json.loads(card_file.with_suffix(".check.json").read_text())
    if rec.get("pdf_sha256") != _manual_sha256(manual):
        return ["record: it was made against another copy of the manual (pdf digest differs)"]
    return []


def read(path: Path) -> Card:
    return load_card(yaml.safe_load(path.read_text(encoding="utf-8")))  # raises ContractError on a broken invariant


def check_card(card: Card, country: str, manuals: Manuals | None, card_file: Path | None = None) -> Loaded:
    now = datetime.now(UTC)
    # The card's own rules (its red zones and rings) hold for every job, so a failure there refuses the whole card.
    # A job's own failure (allow-list, a step's zones, a step or done-when line G0 holds) refuses only that job, with
    # its reason, and the card's other jobs stay usable: one held clause in clean_drain_pump never blocks clean_filters.
    card_fail, rule_ids = rules.check_card(card.model_copy(update={"jobs": []}), country)
    # Only the languages the country's rules file covers are checked here (nl and en for NL, en for uk/us/de): G0 has
    # no table for another language and would refuse the card for that alone. It still fails closed at run time on
    # any sentence in a language the file does not cover. With no rules file at all every line is checked (and held).
    rules_file = rules.load_country(country)
    covered = set(rules_file.languages) if rules_file is not None else None
    refused: dict[str, list[str]] = {}
    for job in card.jobs:
        job_fail, ids = rules.check_card(card.model_copy(update={"jobs": [job]}), country)
        job_fail = [f for f in job_fail if f not in card_fail]
        rule_ids += ids
        for step in job.steps:
            lines = list(step.text.root.items()) + [(k, v) for c in step.done_when for k, v in c.text.root.items()]
            if step.quote is not None and step.quote.en:  # the quote's English, a model's translation: G0 it too
                lines.append(("en", step.quote.en))
            for lang, text in lines:
                if covered is not None and str(lang) not in covered:
                    continue
                r = rules.check_sentence(text, str(lang), card, Ok(state="OK"), country=country)
                if isinstance(r, rules.Hold):
                    job_fail.append(f"{job.id}/{step.id} ({lang}) held by {r.rule_id}")
                    rule_ids.append(r.rule_id)
        if job_fail:
            refused[job.id] = job_fail
    kept = [j for j in card.jobs if j.id not in refused]
    rule_fail = card_fail + [f"job {jid} refused: {f}" for jid, fs in refused.items() for f in fs]
    rules_result = CheckResult(status="fail" if card_fail or not kept else "pass", checked_at=now,
                               failures=rule_fail, rule_ids=sorted(set(rule_ids)) or None)
    cite_fail: list[str] = []
    failed_quotes: set[str] = set()  # quotes[] ids whose citation failed
    pages = pages_of(manuals, card.brand, card.model, manual_household(card_file)) if manuals is not None else []
    cited_by: Literal["manual", "record", "none"] = "none"
    repo = card_file is not None and config.CARDS_DIR.resolve() in card_file.resolve().parents
    if not pages and card_file is not None and repo:
        rec_fail = record_failures(card_file)
        cited_by = "none" if rec_fail else "record"
        cite = CheckResult(status="unchecked" if rec_fail else "pass", checked_at=now,
                           failures=["no manual text in the private folder", *rec_fail] if rec_fail else [])
    elif not pages:
        cite = CheckResult(status="unchecked", checked_at=now, failures=["no manual text in the private folder"])
    else:
        cited_by = "manual"
        # our own step (origin fixit) quotes nothing, so it has nothing to cite; G0 still checks its text above
        quotes: list[tuple[str, int, str]] = [(f"{j.id}/{s.id}", s.quote.page, s.quote.text) for j in card.jobs
                                              for s in j.steps if s.quote is not None]
        quotes += [(f"red_zone/{z.id}", z.quote.page, z.quote.text) for z in card.red_zones]
        quotes += [(f"quote/{q.id}", q.page, q.text) for q in card.quotes]
        for where, page, text in quotes:
            qc = manuals.verify(pages, page, text) if manuals is not None else None
            if qc is None or not qc.verified:
                cite_fail.append(f"{where} p{page}: {qc.reason if qc else 'unchecked'}")
            elif qc.corrected_from is not None:
                cite_fail.append(f"{where}: cited p{page}, found on p{qc.page}")
            else:
                continue
            if where.startswith("quote/"):
                failed_quotes.add(where.removeprefix("quote/"))
        # a failed quote that only needs cite leaves with those needs and the card stays checked; any other fails it
        only_needs = {f"quote/{q}" for q in failed_quotes if q not in quote_refs(card, needs=False)}
        cite_fail = [f for f in cite_fail if f.split(" ")[0].removesuffix(":") not in only_needs]
        cite = CheckResult(status="fail" if cite_fail else "pass", checked_at=now, failures=cite_fail)
    checks = Checks(citation=cite, rules=rules_result)
    rows, rows_held = check_rows(card, {j.id for j in kept}, country, covered)
    kept, needs_held = check_needs(card, kept, failed_quotes, country, covered)
    update: dict[str, Any] = {"checks": checks, **({"jobs": kept} if kept else {})}  # refused jobs leave
    if card.troubleshooting is not None:
        update["troubleshooting"] = rows
    checked = card.model_copy(update=update)
    usable = cite.status == "pass" and rules_result.status == "pass"
    return Loaded(checked, usable, rule_fail + cite.failures, cited_by,
                  {jid: "; ".join(fs) for jid, fs in refused.items()}, rows_held, needs_held)


def quote_refs(card: Card, *, needs: bool) -> set[str]:
    """Every quotes[] id the card cites (done-when lines, after ties, rows ...), with or without its jobs' needs."""
    data = card.model_dump(mode="json", exclude_none=True)
    if not needs:
        for job in data.get("jobs") or []:
            job.pop("needs", None)
    out: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "quote_ref" and isinstance(v, str):
                    out.add(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


def check_needs(card: Card, kept: list[Any], failed_quotes: set[str], country: str,
                covered: set[str] | None) -> tuple[list[Any], dict[str, str]]:
    """The needs a job may list: a card never invents one, so a need whose quote failed the citation check leaves,
    and its words (what) go through G0 in the country's languages, as step text does; a held need leaves with its
    reason. The job itself stays: a list with an item gone is the person getting fewer things to fetch, never a
    step skipped."""
    held: dict[str, str] = {}
    jobs = []
    for job in kept:
        if not job.needs:
            jobs.append(job)
            continue
        keep = []
        for need in job.needs:
            key = f"{job.id}/{slug(need.id)}"
            if slug(need.quote_ref) in failed_quotes:
                held[key] = f"quote {slug(need.quote_ref)} failed the citation check"
                continue
            why = []
            for lang, text in lang_items(need.what):
                if covered is not None and lang not in covered:
                    continue
                r = rules.check_sentence(text, lang, card, Ok(state="OK"), country=country)
                if isinstance(r, rules.Hold):
                    why.append(f"what ({lang}) held by {r.rule_id}")
            if why:
                held[key] = "; ".join(why)
                continue
            keep.append(need)
        jobs.append(job.model_copy(update={"needs": keep}) if len(keep) != len(job.needs) else job)
    return jobs, held


def slug(ref: Any) -> str:
    """A Slug field's text (a RootModel in the generated models)."""
    return str(ref.root if hasattr(ref, "root") else ref)


def lang_items(text: Any) -> list[tuple[str, str]]:
    """A LangText-shaped field (a RootModel, a model with extra language keys, a dict or None) as (lang, text)."""
    if text is None:
        return []
    if hasattr(text, "root"):
        text = text.root
    elif hasattr(text, "model_extra"):
        text = text.model_extra or {}
    return [(str(k), str(v)) for k, v in dict(text).items()]


def check_rows(card: Card, kept: set[str], country: str, covered: set[str] | None) -> tuple[list[Any], dict[str, str]]:
    """The troubleshooting rows a session may show: every text in the row (indicator, symptom, meaning, cause, remedy)
    through G0 in the country's languages, as a step's text is. A held row leaves, with its reason; the card and its
    jobs stay usable. A row's job_ref to a job that left becomes null and the row stays (its remedy is cited), and a
    prevented_by tie to a job that left is dropped. Each row's quote is in card.quotes and cited with the rest."""
    rows: list[Any] = []
    held: dict[str, str] = {}
    for row in card.troubleshooting or []:
        why = []
        for name in ("indicator", "symptom", "meaning", "cause", "remedy"):
            for lang, text in lang_items(getattr(row, name)):
                if covered is not None and lang not in covered:
                    continue
                r = rules.check_sentence(text, lang, card, Ok(state="OK"), country=country)
                if isinstance(r, rules.Hold):
                    why.append(f"{name} ({lang}) held by {r.rule_id}")
        if why:
            held[row.id] = "; ".join(why)
            continue
        fix: dict[str, Any] = {}
        if row.job_ref is not None and slug(row.job_ref) not in kept:
            fix["job_ref"] = None
        ties = [t for t in row.prevented_by if t.job_id in kept]
        if len(ties) != len(row.prevented_by):
            fix["prevented_by"] = ties
        rows.append(row.model_copy(update=fix) if fix else row)
    return rows, held


def load(path: Path, country: str, manuals: Manuals | None) -> Loaded:
    return check_card(read(path), country, manuals, path)
