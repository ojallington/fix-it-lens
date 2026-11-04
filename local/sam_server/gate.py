"""Is our facebook/sam3.1 access approved yet? One HEAD on one small file of the gated repo, with the read token
from ~/.config/fleet/hf-read.token (read by path, sent only as the Authorization header to huggingface.co, never
printed). Prints one word and exits 0 granted, 3 pending, 1 anything else.

    uv run python -m local.sam_server.gate
"""

from __future__ import annotations

import sys

import httpx

from local.sam_server.fetch import REPO, TOKEN

URL = f"https://huggingface.co/{REPO}/resolve/main/config.json"


def status(code: int) -> tuple[str, int]:
    if code in (200, 302, 307):  # a gated file the token may read: served, or redirected to the CDN
        return "granted", 0
    if code in (401, 403):
        return "pending", 3
    return f"unknown (HTTP {code})", 1


def main() -> int:
    if not TOKEN.is_file():
        print(f"no token at {TOKEN}", file=sys.stderr)
        return 1
    token = TOKEN.read_text().strip()
    try:
        r = httpx.head(URL, headers={"Authorization": f"Bearer {token}"}, follow_redirects=False, timeout=15)
    except httpx.HTTPError as e:
        print(f"unknown ({type(e).__name__})")
        return 1
    word, rc = status(r.status_code)
    print(f"facebook/sam3.1: {word}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
