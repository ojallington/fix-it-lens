"""Fetch the SAM 3.1 open weights (gated: access to facebook/sam3.1 on Hugging Face is approved by Meta by hand).

    uv run --project local/sam_server python -m local.sam_server.fetch

The read token is read from ~/.config/fleet/hf-read.token (0600) and passed to the hub client in memory: never in
argv, env, a log or the repo. The weights go to SAM_WEIGHTS (default ~/.cache/fixit/sam3.1), outside the repo, and
are never bundled or committed. SAM 3.1 is under Meta's SAM License, not Apache-2.0.
"""

from __future__ import annotations

import hashlib
import json
import stat
import sys
import time
from pathlib import Path

from local.sam_server.segmenter import CHECKPOINT, WEIGHTS_DIR

TOKEN = Path.home() / ".config" / "fleet" / "hf-read.token"
REPO = "facebook/sam3.1"
FILES = (CHECKPOINT, "config.json", "processor_config.json")


def main() -> int:
    if not TOKEN.is_file():
        print(f"no token at {TOKEN}: make an HF read token and save it there (0600)", file=sys.stderr)
        return 2
    if TOKEN.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"{TOKEN} is readable by others; chmod 600 it first", file=sys.stderr)
        return 2
    from huggingface_hub import get_hf_file_metadata, hf_hub_download, hf_hub_url
    from huggingface_hub.errors import GatedRepoError

    token = TOKEN.read_text().strip()
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for name in FILES:
            print(hf_hub_download(REPO, name, token=token, local_dir=WEIGHTS_DIR))
        want = get_hf_file_metadata(hf_hub_url(REPO, CHECKPOINT), token=token).etag
    except GatedRepoError:
        print(f"{REPO}: access not granted yet (the gate is approved by hand)", file=sys.stderr)
        return 3
    return verify(WEIGHTS_DIR / CHECKPOINT, want)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(path: Path, want: str | None) -> int:
    """The checkpoint's sha256 against the hub's LFS etag (which is the sha256); verified.json records it."""
    got = sha256(path)
    if not want or got != want.strip('"'):
        print(f"{path.name}: sha256 {got} does not match the hub's {want}", file=sys.stderr)
        return 4
    (path.parent / "verified.json").write_text(json.dumps(
        {"repo": REPO, "file": path.name, "sha256": got, "bytes": path.stat().st_size,
         "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
    print(f"verified {path.name} sha256 {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
