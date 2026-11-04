"""No Markdown in anything the guide says (polish #4): a `sentence` message is spoken by TTS and shown as plain text,
so `**bold**`, bullets, quotes, headings, tables or code marks in it are a bug.

    python -m eval.spoken [--sessions DIR]   # every recorded session's sentence messages; exits 1 on any hit

DIR defaults to FIXIT_PRIVATE/sessions (the sessions hold household data, so this runs on the build machine before
each merge, like eval.manualgrep). It prints one claim line (spoken.markdown_sentences, k of n) and, on stderr, the
session and sentence id of each hit, never the text. The whole-context row applies the same test to every answer it
records (wholectx.<mode>.markdown_answers), so that number is replayed by `make eval`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from eval.stats import wilson_count

MARKDOWN = re.compile(
    r"\*\*[^*]+\*\*|__[^_]+__"  # bold
    r"|(?:^|\s)\*[^*\s][^*]*\*(?=\s|$|[.,;:!?])|(?:^|\s)_[^_\s][^_]*_(?=\s|$|[.,;:!?])"  # italics
    r"|^\s*(?:[-*•+]|\d+[.)])\s+"  # a bullet or numbered list item
    r"|^\s*>\s|^\s*#{1,6}\s"  # a quote block or a heading
    r"|`|\|\s*[-:]{3,}|\[[^\]]+\]\([^)]+\)",  # code, a table rule, a link
    re.MULTILINE,
)


def has_markdown(text: str) -> bool:
    return bool(MARKDOWN.search(text))


def sentences(sessions: Path) -> list[tuple[str, str, str]]:
    """(session id, sentence id, text) for every sentence the server sent, in every recorded session."""
    out = []
    for events in sorted(sessions.glob("*/events.jsonl")):
        for line in events.read_text(encoding="utf-8").splitlines():
            e: dict[str, Any] = json.loads(line) if line.strip() else {}
            m = e.get("msg") or {}
            if e.get("dir") == "s2c" and m.get("type") == "sentence":
                out.append((events.parent.name, str(m.get("id")), str(m.get("text", ""))))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.spoken")
    ap.add_argument("--sessions", type=Path)
    a = ap.parse_args(argv)
    root = a.sessions
    if root is None:
        from calls import private_dir

        priv = private_dir()
        if priv is None:
            print("eval.spoken needs FIXIT_PRIVATE (or --sessions DIR)", file=sys.stderr)
            return 2
        root = priv / "sessions"
    said = sentences(root)
    hits = [(s, i) for s, i, t in said if has_markdown(t)]
    for s, i in hits:
        print(f"markdown in session {s} sentence {i}", file=sys.stderr)
    k, n = len(hits), len(said)
    print(json.dumps({"id": "spoken.markdown_sentences", "value": k, "unit": "sentences of n with Markdown", "n": n,
                      "ci": list(wilson_count(k, n)) if n else None}))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
