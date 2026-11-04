"""G0 held-out: sentences the filter's author has never seen, kept sealed so the filter cannot be tuned to them.

    python -m eval.g0_heldout seal SRC.jsonl   # validate, number, encrypt into FIXIT_PRIVATE; write the manifest
    python -m eval.g0_heldout run              # decrypt in memory, ask the rules CLI about each sentence, keep verdicts
    python -m eval.g0_heldout score            # the row: claims from the stored verdicts (replay, no key)

The set was written by a fresh session that saw only eval/heldout/g0-brief.md (no filter code, rules, fixtures or
earlier misses), in a directory outside the project: 300 safe sentences, half English and half Dutch, of which 100
name a red-zone part (warnings, referrals, look-only lines). The brief also asked for unsafe instructions, plain and
reworded; the writer declined those, so the set holds none and the independent unsafe set is the red-team replies
(row g0.unsafe). `seal` still takes unsafe parts, should a person write some.

The sentences never enter the repo. The sealed file is FIXIT_PRIVATE/heldout/g0.jsonl.enc (AES-256, key read by path
from ~/.config/fleet/fixit-g0-heldout.key); eval/heldout/g0.json holds its hashes and the counts per part and
language. `run` writes eval/results/g0_heldout.jsonl: per sentence only its id, part, language and verdict, never its
text or the rule that fired, so the results say how many were missed and not which words to add. Each `run` reveals
the counts again, so it is run sparingly (a baseline, then once when the filter work is done), never in a loop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from eval.stats import wilson_count

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
# name -> (claim id prefix, sentence id prefix). The first set is spent (measured on three rules trees); the second
# was measured once and its counts then reached the filter's author, so it is spent too; the third was written the
# same way for the rules that ship, and is measured once; the fourth is written to the first brief (unsafe parts too),
# kept sealed, and run only when asked.
SETS = {"g0": ("g0.heldout", "ho"), "g0b": ("g0.heldout2", "hb"), "g0c": ("g0.heldout3", "hc"),
        "g0d": ("g0.heldout4", "hd")}
MANIFEST = EVAL / "heldout" / "g0.json"
RESULTS = EVAL / "results" / "g0_heldout.jsonl"


def paths(name: str) -> tuple[Path, Path]:
    """(manifest, results) of a sealed set."""
    if name not in SETS:
        raise HeldoutError(f"no held-out set {name!r}")
    return EVAL / "heldout" / f"{name}.json", EVAL / "results" / f"{name}_heldout.jsonl"
KEY = Path.home() / ".config" / "fleet" / "fixit-g0-heldout.key"
PARTS = ("safe", "safe_redzone", "unsafe_plain", "unsafe_reworded")
LANGS = ("en", "nl")
APPLIANCES = {"washer", "dryer", "dishwasher", "fridge", "oven_hob", "hood", "boiler", "general"}
COUNTRY = "nl"
NEAR = 90  # rapidfuzz ratio at or above which two sentences count as near-copies
OPENSSL = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt"]


class HeldoutError(Exception):
    pass


def sealed_path(name: str = "g0") -> Path:
    priv = os.environ.get("FIXIT_PRIVATE")
    base = Path(priv) if priv else Path.home() / "Projects/hackathons/fix-it-lens-private"
    return base / "heldout" / f"{name}.jsonl.enc"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _norm(t: str) -> str:
    return " ".join(t.lower().split())


def validate(rows: list[dict[str, Any]], against: list[str]) -> list[str]:
    """Problems with a written set: schema, sentences that would break the line-per-sentence CLI, near-copies inside the
    set and of any sentence in `against` (the filter's own test sets)."""
    from rapidfuzz import fuzz, process

    out = []
    for i, r in enumerate(rows, 1):
        if set(r) != {"set", "lang", "appliance", "text"}:
            out.append(f"line {i}: fields {sorted(r)}")
        elif r["set"] not in PARTS or r["lang"] not in LANGS or r["appliance"] not in APPLIANCES:
            out.append(f"line {i}: set/lang/appliance {r['set']}/{r['lang']}/{r['appliance']}")
        elif not isinstance(r["text"], str) or not r["text"].strip() or "\n" in r["text"] or len(r["text"]) > 300:
            out.append(f"line {i}: text must be one non-empty line")
    if out:
        return out
    texts = [_norm(r["text"]) for r in rows]
    for i, t in enumerate(texts):
        for _, score, j in process.extract(t, texts[i + 1:], scorer=fuzz.ratio, score_cutoff=NEAR, limit=None):
            out.append(f"lines {i + 1} and {i + 2 + j} are near-copies ({score:.0f})")
    theirs = [_norm(x) for x in against]
    for i, t in enumerate(texts):
        if theirs and process.extractOne(t, theirs, scorer=fuzz.ratio, score_cutoff=NEAR):
            out.append(f"line {i + 1} is a near-copy of a sentence in the filter's own test sets")
    return out


def filter_sets(at: str | None = None) -> list[str]:
    """Every sentence in the filter's own fixtures (fixtures/l4/g0 and below), to keep the held-out set disjoint from
    them; with `at`, as they are at that commit (the rules branch about to be measured)."""
    import tomllib

    fix = "fixtures/l4/g0"
    if at:
        names = subprocess.run(["git", "ls-tree", "-r", "--name-only", at, fix], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.split()

        def read(n: str) -> str:
            return subprocess.run(["git", "show", f"{at}:{n}"], cwd=ROOT, capture_output=True, text=True,
                                  check=True).stdout
    else:
        names = sorted(str(p.relative_to(ROOT)) for p in (ROOT / fix).rglob("*") if p.is_file())

        def read(n: str) -> str:
            return (ROOT / n).read_text()
    out: list[str] = []

    def strings(v: Any) -> None:
        if isinstance(v, str) and len(v.split()) >= 4:
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                strings(x)
        elif isinstance(v, list):
            for x in v:
                strings(x)

    for n in sorted(names):
        if n.endswith(".jsonl"):
            out += [json.loads(x)["text"] for x in read(n).splitlines() if x.strip()]
        elif n.endswith(".toml"):
            strings(tomllib.loads(read(n)))
    return out


def counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {p: {lang: sum(1 for r in rows if r["set"] == p and r["lang"] == lang) for lang in LANGS} for p in PARTS}


def _key(create: bool) -> Path:
    if not KEY.exists():
        if not create:
            raise HeldoutError(f"no key at {KEY}: only the eval session runs the held-out set")
        KEY.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32) + "\n")
    if KEY.stat().st_mode & 0o077:
        raise HeldoutError(f"{KEY} must be 0600")
    return KEY


def seal(src: Path, *, sealed: Path | None = None, manifest: Path = MANIFEST, brief: Path | None = None,
         against: list[str] | None = None, id_prefix: str = "ho") -> dict[str, Any]:
    rows = [json.loads(x) for x in src.read_text().splitlines() if x.strip()]
    problems = validate(rows, filter_sets() if against is None else against)
    if problems:
        raise HeldoutError("not sealed:\n  " + "\n  ".join(problems))
    # Ids follow the hash of the text, so their order says nothing about which part a sentence is in.
    rows.sort(key=lambda r: sha256(r["text"].encode()))
    plain = "".join(json.dumps({"id": f"{id_prefix}{i + 1:03d}", **r}, ensure_ascii=False) + "\n"
                    for i, r in enumerate(rows)).encode()
    out = sealed or sealed_path()
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    enc = subprocess.run([*OPENSSL, "-pass", f"file:{_key(create=True)}"], input=plain, capture_output=True,
                         check=True).stdout
    out.write_bytes(enc)
    out.chmod(0o600)
    m: dict[str, Any] = {"n": len(rows), "parts": counts(rows), "sha256_plain": sha256(plain),
                         "sha256_sealed": sha256(enc)}
    if brief is not None:
        m["brief_sha256"] = sha256(brief.read_bytes())
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(m, indent=2) + "\n")
    return m


def unseal(sealed: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    enc = sealed.read_bytes()
    if sha256(enc) != manifest["sha256_sealed"]:
        raise HeldoutError("the sealed file does not match the manifest")
    plain = subprocess.run([*OPENSSL, "-d", "-pass", f"file:{_key(create=False)}"], input=enc, capture_output=True,
                           check=True).stdout
    if sha256(plain) != manifest["sha256_plain"]:
        raise HeldoutError("the unsealed set does not match the manifest")
    return [json.loads(x) for x in plain.decode().splitlines()]


def rules_version(commit: str | None = None) -> str:
    """The rules directory the verdicts came from: its git tree, and whether the working copy differs from it. The
    tree, not the commit, names it, so a run on a branch's commit still matches once the same rules reach main."""
    tree = subprocess.run(["git", "rev-parse", f"{commit or 'HEAD'}:rules"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()
    if commit:
        return tree[:12]
    dirty = subprocess.run(["git", "status", "--porcelain", "rules"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.strip()
    return tree[:12] + ("+changes" if dirty else "")


# The G0 filter: the files `python -m rules check-sentence` loads that decide a verdict, with or without a card. That
# is its rules modules and the data they read, and the contracts that shape how nl.yaml loads and what a verdict is.
# The check loads more of contracts, and those files are named in NOT_FILTER instead. tests/l6/test_g0_heldout.py
# reruns the check under an audit hook and fails both ways: when it loads a file in neither list, and when a file in
# FILTER_FILES is no longer loaded.
FILTER_FILES = tuple(f"rules/{f}" for f in (
    "__init__.py", "__main__.py", "cards.py", "cli.py", "compose.py", "country.py", "disclaimer.py", "engine.py",
    "g0.py", "landlord.py", "reading.py", "steps.py", "text.py", "disclaimer.yaml", "nl.yaml")) + (
    "contracts/models/rules.py", "contracts/models/safety.py", "contracts/rules.schema.json",
    "contracts/safety.schema.json")
# Loaded, but they are not the filter. contracts/__init__.py validates every contract and reads every schema at import,
# so it loads the others too. The card's model, schema and load invariants decide whether a card loads, not what the
# filter does with a sentence, so live G0 (which runs with a card) is checked by a replay with the Bosch card.
NOT_FILTER = ("contracts/VERSION", "contracts/__init__.py", "contracts/models/__init__.py", "contracts/models/card.py",
              "contracts/card.schema.json", "contracts/claim.schema.json", "contracts/feedback.schema.json",
              "contracts/g1.schema.json", "contracts/judge.schema.json", "contracts/ledger.schema.json",
              "contracts/lookup_makers.json", "contracts/session.schema.json", "contracts/tools.schema.json",
              "contracts/ws.schema.json")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def _filter_at(commit: str) -> str:
    blobs = _git("rev-parse", *(f"{commit}:{f}" for f in FILTER_FILES)).split()
    if len(blobs) != len(FILTER_FILES):  # an older commit lacks a file: it counts as missing
        blobs = [_git("rev-parse", "--verify", "--quiet", f"{commit}:{f}") or "missing" for f in FILTER_FILES]
    return hashlib.sha256("".join(f"{f} {b}\n" for f, b in zip(FILTER_FILES, blobs, strict=True)).encode()).hexdigest()


def filter_version(at: str | None = None) -> str:
    """The G0 filter a verdict came from: 12 hex of a hash over the git blob ids of FILTER_FILES at a commit, so a
    commit that changes nothing they hold (rules/eval.py, say) keeps the id. A rules tree (what the results store) is
    read at the commits that have it: "ambiguous" if they disagree, "unknown" if none or the name is not git's. Without
    at: HEAD, marked when the working copy differs in those files."""
    at = at or "HEAD"
    kind = _git("cat-file", "-t", at)
    if kind == "commit":
        dirty = at == "HEAD" and _git("status", "--porcelain", "--", *FILTER_FILES)
        return _filter_at(at)[:12] + ("+changes" if dirty else "")
    if kind != "tree":
        return "unknown"
    tree = _git("rev-parse", at)
    commits = _git("rev-list", "--all").split()
    trees = _git("rev-parse", *(f"{c}:rules" for c in commits)).split()
    ids = {_filter_at(c)[:12] for c, t in zip(commits, trees, strict=True) if t == tree}
    return ids.pop() if len(ids) == 1 else "ambiguous" if ids else "unknown"


def verdicts(rows: list[dict[str, Any]], cwd: Path = ROOT, card: str | None = None) -> dict[str, bool]:
    """id -> held back, from the rules CLI (eval never imports rules), one call per language."""
    out: dict[str, bool] = {}
    for lang in LANGS:
        mine = [r for r in rows if r["lang"] == lang]
        if not mine:
            continue
        p = subprocess.run([sys.executable, "-m", "rules", "check-sentence", "--country", COUNTRY, "--lang", lang,
                            "--jsonl", *(["--card", card] if card else [])], cwd=cwd,
                           input="".join(r["text"] + "\n" for r in mine), capture_output=True, text=True, check=True)
        got = [json.loads(x) for x in p.stdout.splitlines() if x.strip()]
        if len(got) != len(mine):
            raise HeldoutError(f"rules CLI answered {len(got)} of {len(mine)} {lang} sentences")
        for r, g in zip(mine, got, strict=True):
            out[r["id"]] = g["result"] == "hold"
    return out


def run(*, sealed: Path | None = None, manifest: Path = MANIFEST, results: Path = RESULTS,
        rules_at: str | None = None) -> None:
    """Verdicts for the whole sealed set. With rules_at, the rules CLI runs from a throwaway checkout of that commit
    (a branch not merged here yet), and this working copy is left as it is."""
    m = json.loads(manifest.read_text())
    rows = unseal(sealed or sealed_path(), m)
    if rules_at:
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "rules-at"
            subprocess.run(["git", "worktree", "add", "--detach", str(wt), rules_at], cwd=ROOT, capture_output=True,
                           check=True)
            try:
                held = verdicts(rows, cwd=wt)
            finally:
                subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)
    else:
        held = verdicts(rows)
    version = rules_version(rules_at)
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text("".join(json.dumps({"id": r["id"], "set": r["set"], "lang": r["lang"], "held": held[r["id"]],
                                           "rules": version}) + "\n" for r in rows))


# claim id suffix, the parts it counts, its unit
CLAIMS = (("safe.wrongly_struck", ("safe", "safe_redzone"), "safe sentences wrongly struck"),
          ("safe_redzone.wrongly_struck", ("safe_redzone",),
           "safe sentences naming a red-zone part wrongly struck (part of the safe count)"),
          ("unsafe.plain.struck", ("unsafe_plain",), "sentences struck"),
          ("unsafe.reworded.struck", ("unsafe_reworded",), "sentences struck"))


def claims(results: list[dict[str, Any]], manifest: dict[str, Any], prefix: str = "g0.heldout") -> list[dict[str, Any]]:
    """One claim per kind of sentence the sealed set holds; a part it does not hold (the unsafe parts, today) prints
    nothing, so it is never listed as pending either."""
    size = {p: sum(manifest["parts"][p].values()) for p in PARTS}
    specs = [(f"{prefix}.{x}", parts, unit) for x, parts, unit in CLAIMS if sum(size[p] for p in parts)]
    if not results:
        return [{"id": cid, "value": None, "unit": unit, "n": 0, "ci": None} for cid, _, unit in specs]
    if counts(results) != manifest["parts"]:
        raise HeldoutError("the results do not cover the sealed set (re-run `python -m eval.g0_heldout run`)")
    [version] = {r["rules"] for r in results}
    n_safe = size["safe"] + size["safe_redzone"]
    targets = {f"{prefix}.safe.wrongly_struck": f"at most {n_safe // 100} of {n_safe}",
               f"{prefix}.unsafe.plain.struck": "all", f"{prefix}.unsafe.reworded.struck": "all"}
    out = []
    for cid, parts, unit in specs:
        rs = [r for r in results if r["set"] in parts]
        k = sum(1 for r in rs if r["held"])
        by = ", ".join(f"{lang} {sum(1 for r in rs if r['lang'] == lang and r['held'])}/"
                       f"{sum(1 for r in rs if r['lang'] == lang)}" for lang in LANGS)
        c = {"id": cid, "value": k, "unit": unit, "n": len(rs), "ci": list(wilson_count(k, len(rs))),
             "note": f"sealed held-out set, written without sight of the filter; G0 filter {filter_version(version)} "
                     f"(rules tree {version}); {by}"}
        out.append({**c, "target": targets[cid]} if cid in targets else c)
    return out


# the parts a set is written to hold, so its claims are listed as not measured yet before it is sealed
PLANNED = {s: {"safe": {"en": 100, "nl": 100}, "safe_redzone": {"en": 50, "nl": 50},
               "unsafe_plain": {"en": 0, "nl": 0}, "unsafe_reworded": {"en": 0, "nl": 0}} for s in ("g0b", "g0c")}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.g0_heldout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seal")
    s.add_argument("src", type=Path)
    s.add_argument("--brief", type=Path, default=EVAL / "heldout" / "g0-brief.md")
    s.add_argument("--against-at", help="check overlap with the filter's fixtures as they are at this commit")
    s.add_argument("--unlike", action="append", default=[], help="also keep clear of this sealed set's sentences")
    r = sub.add_parser("run")
    r.add_argument("--rules-at", help="a commit whose rules/ to run, from a throwaway checkout")
    r.add_argument("--out", type=Path, help="where the verdicts go (default: the set's results file)")
    sc = sub.add_parser("score")
    fi = sub.add_parser("filter-id", help="the G0 filter's id: a hash over the files the sentence check loads")
    fi.add_argument("--at", help="a commit, or a rules tree (default: HEAD, marked if the working copy differs)")
    for p in (s, r, sc):
        p.add_argument("--set", dest="name", default="g0", choices=sorted(SETS))
    a = ap.parse_args(argv)
    if a.cmd == "filter-id":
        print(filter_version(a.at))
        return 0
    prefix, id_prefix = SETS[a.name]
    manifest, results = paths(a.name)
    try:
        if a.cmd == "seal":
            against = filter_sets(a.against_at)
            for other in a.unlike:
                m_other, _ = paths(other)
                against += [r["text"] for r in unseal(sealed_path(other), json.loads(m_other.read_text()))]
            print(json.dumps(seal(a.src, sealed=sealed_path(a.name), manifest=manifest, brief=a.brief,
                                  against=against, id_prefix=id_prefix)))
        elif a.cmd == "run":
            out = a.out or results
            run(sealed=sealed_path(a.name), manifest=manifest, results=out, rules_at=a.rules_at)
            print(f"wrote {out}; score with `python -m eval.g0_heldout score --set {a.name}`")
        else:
            m = json.loads(manifest.read_text()) if manifest.exists() else {"parts": PLANNED[a.name]}
            rs = [json.loads(x) for x in results.read_text().splitlines() if x.strip()] if results.exists() else []
            for c in claims(rs, m, prefix):
                print(json.dumps(c, ensure_ascii=False))
    except HeldoutError as e:
        print(f"g0_heldout: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
