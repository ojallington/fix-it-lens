"""Score a built card against its gold card (eval/gold.py), by code: one row per card and a total.

Matching. A built job's text is its titles, step texts, quotes (and their English) and done-when clauses, lower
case. A gold job is covered by the built job that scores best on 0.4 × (any of the gold job's `match` words is in
the built text) + 0.6 × (the mean, over the gold job's steps, of the best step similarity), when that is at least
JOB_MIN and the step mean alone at least JOB_STEP_MIN; two gold jobs may be covered by the same built job. Step
similarity is the larger of the two quotes' word overlap and the two English texts' word overlap (Dice: 2 × shared
words / all words, short and common words left out). Within a covered job, gold and built steps pair one to one,
most similar first; a gold step is carried when its pair reaches STEP_MIN.

Columns:
    jobs      gold jobs covered / gold jobs
    main      the built card's first job covers gold's main job (1/0)
    steps     gold steps carried / gold steps (of every gold job; an uncovered job's steps count as missed)
    order     carried steps of a job that come in gold's order (longest in-order run) / carried steps
    warn      gold warnings carried / gold warnings: a warning's `match` word in the covering job's text, or a built
              quote holding WARN_MIN of the warning quote's words
    refit     gold steps with role refit carried / such steps
    power     gold unplug and plug_back steps carried / such steps
    pages     carried steps whose built page is gold's page or one of its alt_pages / carried steps
    wrong     items that are wrong, by code: a built job that covers a gold professional-only job, a built quote that
              is on no page of the manual (when the manual text is at hand; a quote under 20 characters is not
              judged), and an interval the manual does not
              give (built interval where gold has none, or one outside gold's interval and its interval_ok range);
              a built job that names a gold user job (one of its match words) is extra, never professional
    extra     built jobs that cover no gold job and no professional one (not counted wrong: gold may have missed
              a job, which a read decides)

score = 100 × (0.25 jobs + 0.10 main + 0.20 steps + 0.15 warn + 0.15 (refit and power together) + 0.15 pages)
        − 5 × wrong, at least 0. A card with no built card scores 0 and is shown as missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

JOB_MIN = 0.35
JOB_STEP_MIN = 0.3
STEP_MIN = 0.45
WARN_MIN = 0.6
WEIGHTS = {"jobs": 0.25, "main": 0.10, "steps": 0.20, "warn": 0.15, "refit_power": 0.15, "pages": 0.15}
WRONG_COST = 5.0

_STOP = (
    "the and for with from that this then into onto your you are not can will has have was any all its out off "
    "when where which what there their them also only must should may use using used each one two more than "
    "de het een en van voor met naar dat die dit dan als bij aan uit ook niet wel zijn wordt worden kan moet "
    "mag uw u of op te in er om tot door over na nog zo"
)
STOP = set(_STOP.split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]+|\d+", text.lower()) if len(w) > 2 and w not in STOP}


def overlap(a: str, b: str) -> float:
    """Dice: 2 × shared words / all words of both."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return 2 * len(wa & wb) / (len(wa) + len(wb))


def contained(part: str, whole: str) -> float:
    """Share of part's words that are in whole."""
    wp = words(part)
    return len(wp & words(whole)) / len(wp) if wp else 0.0


def _lang_text(v: Any) -> str:
    if isinstance(v, dict):
        return " ".join(str(x) for x in v.values() if x)
    return str(v or "")


def _en(v: Any) -> str:
    return str(v.get("en") or "") if isinstance(v, dict) else str(v or "")


def step_quote(s: dict[str, Any]) -> str:
    q = s.get("quote") or {}
    return str(q.get("text") or "") if isinstance(q, dict) else str(q)


def step_sim(built: dict[str, Any], gold: dict[str, Any]) -> float:
    return max(overlap(step_quote(built), step_quote(gold)), overlap(_en(built.get("text")), _en(gold.get("text"))))


def job_text(job: dict[str, Any]) -> str:
    parts = [_lang_text(job.get("title"))]
    for s in job.get("steps") or []:
        q = s.get("quote") or {}
        parts += [_lang_text(s.get("text")), step_quote(s), str(q.get("en") or "") if isinstance(q, dict) else ""]
        parts += [_lang_text(c.get("text")) for c in s.get("done_when") or []]
    return " ".join(parts).lower()


def has_word(text: str, match: list[str]) -> bool:
    return any(m.lower() in text for m in match)


@dataclass
class Row:
    key: str
    missing: bool = False
    n: dict[str, tuple[int, int]] = field(default_factory=dict)  # column -> (hits, of)
    wrong: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    def frac(self, col: str) -> float | None:
        hit, of = self.n.get(col, (0, 0))
        return hit / of if of else None

    @property
    def score(self) -> float:
        if self.missing:
            return 0.0
        rp = (self.n["refit"][0] + self.n["power"][0], self.n["refit"][1] + self.n["power"][1])
        parts = {"jobs": self.frac("jobs"), "main": self.frac("main"), "steps": self.frac("steps"),
                 "warn": self.frac("warn"), "refit_power": rp[0] / rp[1] if rp[1] else None,
                 "pages": self.frac("pages")}
        # a column with nothing to score (no warnings in gold) gives its weight to the others
        got = {k: v for k, v in parts.items() if v is not None}
        total_w = sum(WEIGHTS[k] for k in got)
        base = sum(WEIGHTS[k] * v for k, v in got.items()) / total_w if total_w else 0.0
        return max(0.0, 100 * base - WRONG_COST * len(self.wrong))


def _interval(job: dict[str, Any]) -> tuple[int, str] | None:
    iv = job.get("interval")
    if not isinstance(iv, dict):
        return None
    return int(iv.get("every") or 0), str(iv.get("unit") or "")


DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def same_interval(a: tuple[int, str] | None, b: tuple[int, str] | None) -> bool:
    """Equal within a quarter: "twice a year" as 6 months is 26 weeks; cycles compare only with cycles."""
    if a is None or b is None:
        return a == b
    if (a[1] == "cycle") != (b[1] == "cycle"):
        return False
    da, db = (a[0], b[0]) if a[1] == "cycle" else (a[0] * DAYS.get(a[1], 0), b[0] * DAYS.get(b[1], 0))
    return da > 0 and db > 0 and abs(da - db) <= 0.25 * max(da, db)


def _in_order(seq: list[int]) -> int:
    """Length of the longest increasing run (not necessarily adjacent) of gold positions."""
    best: list[int] = []
    for x in seq:
        i = next((k for k, b in enumerate(best) if b >= x), len(best))
        best[i:i + 1] = [x]
    return len(best)


def score_card(key: str, built: dict[str, Any] | None, gold: dict[str, Any],
               pages: list[str] | None = None) -> Row:
    row = Row(key)
    notes = gold.get("gold_notes") or {}
    gjobs = gold.get("jobs") or []
    if built is None:
        row.missing = True
        return row
    bjobs = [j for j in built.get("jobs") or [] if isinstance(j, dict)]
    # a built card that keeps warnings beside its steps (another gold reading does) carries them there too
    bwarn = {jid: n.get("warnings") or [] for jid, n in ((built.get("gold_notes") or {}).get("jobs") or {}).items()}
    btexts = [" ".join([job_text(j)] + [f"{w.get('quote', '')} {w.get('en', '')}".lower()
                                        for w in bwarn.get(str(j.get("id")), [])]) for j in bjobs]
    njobs = notes.get("jobs") or {}

    def cover(gj: dict[str, Any]) -> tuple[int | None, float]:
        best: tuple[int | None, float] = (None, 0.0)
        gsteps = gj.get("steps") or []
        for i, bj in enumerate(bjobs):
            kw = 1.0 if has_word(btexts[i], (njobs.get(gj["id"]) or {}).get("match") or []) else 0.0
            st = sum(max((step_sim(b, g) for b in bj.get("steps") or []), default=0.0) for g in gsteps)
            mean = st / len(gsteps) if gsteps else 0.0
            v = 0.4 * kw + 0.6 * mean
            if v > best[1] and mean >= JOB_STEP_MIN:  # a shared word alone never covers a job
                best = (i, v)
        return best if best[1] >= JOB_MIN else (None, best[1])

    covered: dict[str, int] = {}
    strength: dict[str, float] = {}
    for gj in gjobs:
        i, v = cover(gj)
        if i is not None:
            covered[gj["id"]] = i
            strength[gj["id"]] = v
    row.n["jobs"] = (len(covered), len(gjobs))
    main = str(notes.get("main_job"))
    row.n["main"] = (int(bool(bjobs) and covered.get(main) == 0), 1)

    steps = order = pages_ok = carried_total = warn_hit = warn_of = 0
    refit = [0, 0]
    power = [0, 0]
    for gj in gjobs:
        n = njobs.get(gj["id"]) or {}
        roles = n.get("roles") or {}
        alt = n.get("alt_pages") or {}
        power_ids = {n.get("unplug"), n.get("plug_back")} - {None}
        warnings = n.get("warnings") or []
        warn_of += len(warnings)
        gsteps = gj.get("steps") or []
        refit[1] += sum(1 for g in gsteps if roles.get(g["id"]) == "refit")
        power[1] += sum(1 for g in gsteps if g["id"] in power_ids)
        if gj["id"] not in covered:
            continue
        bj = bjobs[covered[gj["id"]]]
        bsteps = bj.get("steps") or []
        # each built step carries at most one gold step: best pairs first
        pairs = sorted(((step_sim(b, g), gi, k) for gi, g in enumerate(gsteps) for k, b in enumerate(bsteps)),
                       reverse=True)
        took: dict[int, int] = {}
        for sim, gi, k in pairs:
            if sim >= STEP_MIN and gi not in took and k not in took.values():
                took[gi] = k
        positions: list[int] = []  # built position of each carried gold step, in gold order
        for gi, g in enumerate(gsteps):
            if gi not in took:
                continue
            k = took[gi]
            steps += 1
            positions.append(k)
            if roles.get(g["id"]) == "refit":
                refit[0] += 1
            if g["id"] in power_ids:
                power[0] += 1
            b_page = bsteps[k].get("page")
            if b_page == g.get("page") or b_page in (alt.get(g["id"]) or []):
                pages_ok += 1
        carried_total += len(positions)
        order += _in_order(positions)
        text = btexts[covered[gj["id"]]]
        bquotes = [step_quote(b) for b in bsteps] + [str(w.get("quote") or "")
                                                    for w in bwarn.get(str(bj.get("id")), [])]
        for w in warnings:
            if has_word(text, w.get("match") or []) or any(contained(w.get("quote") or "", q) >= WARN_MIN
                                                          for q in bquotes):
                warn_hit += 1
    row.n["steps"] = (steps, sum(len(g.get("steps") or []) for g in gjobs))
    row.n["order"] = (order, carried_total)
    row.n["pages"] = (pages_ok, carried_total)
    row.n["warn"] = (warn_hit, warn_of)
    row.n["refit"] = (refit[0], refit[1])
    row.n["power"] = (power[0], power[1])

    used = set(covered.values())
    gold_by_id = {g["id"]: g for g in gjobs}
    for i, bj in enumerate(bjobs):
        jid = str(bj.get("id"))
        pro = [p for p in notes.get("professional") or [] if has_word(btexts[i], p.get("match") or [])]
        # a fragment of a user job (it names the job but covers too little of it) is extra, not professional work
        names_user_job = any(has_word(btexts[i], n.get("match") or []) for n in njobs.values())
        if pro and i not in used and not names_user_job:
            row.wrong.append(f"{jid}: offers professional-only work ({pro[0]['title']})")
        elif i not in used:
            row.extra.append(jid)
    best_gold: dict[int, str] = {}  # each built job's interval is judged once, against the gold job it covers best
    for gid, i in covered.items():
        if i not in best_gold or strength[gid] > strength[best_gold[i]]:
            best_gold[i] = gid
    for i, gid in best_gold.items():
        b_iv, g_iv = _interval(bjobs[i]), _interval(gold_by_id[gid])
        also = [(int(e), str(u)) for e, u in (njobs.get(gid) or {}).get("interval_ok") or []]  # a stated range
        if b_iv is not None and not any(same_interval(b_iv, ok) for ok in [g_iv, *also]):
            want = f"every {g_iv[0]} {g_iv[1]}" if g_iv else "none in the manual"
            row.wrong.append(f"{bjobs[i].get('id')}: interval every {b_iv[0]} {b_iv[1]}, manual: {want}")
    if pages is not None:
        from ingest.citation import verify

        for bj in bjobs:
            for s in bj.get("steps") or []:
                q = step_quote(s)
                r = verify(pages, int(s.get("page") or 0), q) if q else None
                if r is not None and not r.ok and r.reason != "too_short":  # a short real line is no invention
                    row.wrong.append(f"{bj.get('id')}/{s.get('id')}: quote is on no page of the manual")
    return row


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{100 * v:.0f}%"


def table(rows: list[Row]) -> str:
    cols = ["jobs", "main", "steps", "order", "warn", "refit", "power", "pages"]
    head = "| card | " + " | ".join(cols) + " | wrong | extra | score |"
    lines = [head, "|" + "---|" * (len(cols) + 4)]
    for r in rows:
        if r.missing:
            lines.append(f"| {r.key} | " + " | ".join("–" for _ in cols) + " | – | – | 0 (no card) |")
            continue
        cells = [f"{r.n[c][0]}/{r.n[c][1]}" for c in cols]
        lines.append(f"| {r.key} | " + " | ".join(cells) + f" | {len(r.wrong)} | {len(r.extra)} | {r.score:.0f} |")
    tot = {c: (sum(r.n.get(c, (0, 0))[0] for r in rows), sum(r.n.get(c, (0, 0))[1] for r in rows)) for c in cols}
    lines.append("| **total** | " + " | ".join(_pct(tot[c][0] / tot[c][1] if tot[c][1] else None) for c in cols)
                 + f" | {sum(len(r.wrong) for r in rows)} | {sum(len(r.extra) for r in rows)} | "
                 f"{sum(r.score for r in rows) / len(rows):.0f} |" if rows else "")
    detail = [f"- {r.key}: {w}" for r in rows for w in r.wrong]
    return "\n".join(lines + (["", "Wrong:", *detail] if detail else []))


def from_raw(draft: dict[str, Any], page_map: list[int] | None = None) -> dict[str, Any]:
    """A raw whole-manual draft (title_en/title_manual, text_en/text_manual, quote and quote_en as strings, pages
    numbered by the pages sent) as the card-shaped dict score_card reads, its pages mapped back to the manual's:
    draft page k is manual page page_map[k-1]; no map means the whole PDF was sent."""
    lang = str(draft.get("manual_lang") or "xx")

    def page(k: Any) -> int:
        k = int(k or 0)
        return int(page_map[k - 1]) if page_map and 1 <= k <= len(page_map) else k

    def texts(o: dict[str, Any], stem: str) -> dict[str, str]:
        out = {"en": str(o.get(f"{stem}_en") or "")}
        if o.get(f"{stem}_manual"):
            out[lang] = str(o[f"{stem}_manual"])
        return out

    jobs = []
    for j in draft.get("jobs") or []:
        steps = [{"id": s.get("id"), "text": texts(s, "text"), "page": page(s.get("page")),
                  "quote": {"text": str(s.get("quote") or ""), "en": str(s.get("quote_en") or "")},
                  "done_when": [{"text": {"en": str(c.get("text_en") or "")}} for c in s.get("done_when") or []]}
                 for s in j.get("steps") or []]
        jobs.append({"id": j.get("id"), "title": texts(j, "title"), "kind": j.get("kind"),
                     "interval": j.get("interval"), "steps": steps})
    return {"jobs": jobs}


def score_raw_dir(drafts: Path, gold_root: Path) -> list[Row]:
    """Every gold card against the raw draft <brand>-<model slug>.draft.json in drafts, mapped by
    drafts/page_map.json; a gold card with no draft is a missing row."""
    import json

    from eval import gold as goldmod

    maps = json.loads((drafts / "page_map.json").read_text()) if (drafts / "page_map.json").exists() else {}
    rows = []
    for g in sorted(gold_root.glob("*.yaml")):
        name = g.stem.replace("__", "-", 1)
        f = drafts / f"{name}.draft.json"
        built = from_raw(json.loads(f.read_text()), maps.get(name)) if f.exists() else None
        gold = yaml.safe_load(g.read_text(encoding="utf-8"))
        rows.append(score_card(g.stem, built, gold, goldmod.page_texts(gold_root, g.stem)))
    return rows


def draft_key(path: Path, data: dict[str, Any]) -> str:
    return path.stem if "__" in path.stem else f"{data.get('brand')}__{path.stem}"


def score_dir(drafts: Path, gold_root: Path, *, only_found: bool = False, pages_root: Path | None = None) -> list[Row]:
    """Every gold card in gold_root against the draft with its key under drafts (<brand>/<model>.yaml or flat
    <key>.yaml); the manual's pages from (pages_root or gold_root)/manuals.toml when present. only_found leaves out
    gold cards with no draft (the second reading covers a few manuals only)."""
    from eval import gold as goldmod

    found: dict[str, dict[str, Any]] = {}
    for f in sorted(drafts.rglob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "jobs" in data:
            found[draft_key(f, data)] = data
    proot = pages_root or gold_root
    known = goldmod.manuals(proot) if (proot / "manuals.toml").exists() else {}
    rows = []
    for g in sorted(gold_root.glob("*.yaml")):
        if only_found and g.stem not in found:
            continue
        gold = yaml.safe_load(g.read_text(encoding="utf-8"))
        pages = goldmod.page_texts(proot, g.stem) if g.stem in known else None
        rows.append(score_card(g.stem, found.get(g.stem), gold, pages))
    return rows
