"""stdio, for a local agent: the token is read from a file (0600), never from argv or the environment.

    uv run python -m integrations.mcp_server --origin https://<host> --token-file ~/.config/fixit/token [--read-only]
"""

from __future__ import annotations

import argparse
import stat
from pathlib import Path

import httpx

from integrations.mcp_server.server import Headers, build


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m integrations.mcp_server", description=__doc__.splitlines()[0])
    ap.add_argument("--origin", required=True, help="the app's address, e.g. https://fixit.example")
    ap.add_argument("--token-file", type=Path, required=True, help="a file holding the token (0600)")
    ap.add_argument("--read-only", action="store_true", help="offer the read tools only")
    return ap


def main() -> None:
    args = parser().parse_args()
    path: Path = args.token_file.expanduser()
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f"{path}: readable by others; chmod 600 it first")
    token = path.read_text().strip()
    origin = args.origin.rstrip("/")

    def rest(_: Headers) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=origin, headers={"Authorization": f"Bearer {token}"}, timeout=20)

    build(rest, lambda _: origin, (lambda _: "read") if args.read_only else None).run("stdio")


if __name__ == "__main__":
    main()
