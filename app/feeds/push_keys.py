"""Make the deployment's VAPID key once (0600, by path) and print its public key.

    uv run python -m app.feeds.push_keys
"""

from __future__ import annotations

import os
from pathlib import Path

from app.feeds.push import DEFAULT_VAPID, load_vapid

if __name__ == "__main__":
    v = load_vapid(Path(os.environ.get("FIXIT_VAPID", DEFAULT_VAPID)), create=True)
    print(f"{v.pem_path}\n{v.public_key}")
