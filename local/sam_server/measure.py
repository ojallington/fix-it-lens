"""stage.sh measure: one cold and one warm SAM 3.1 call through calls/ (local mode, $0 in the ledger), and the MiB
the server's process holds on the GPU afterwards, for the build log and the GPU host's residency register.

    FIXIT_LOCAL_SAM_URL=http://<gpu-host>:8088 uv run python -m local.sam_server.measure

The GPU host is FIXIT_LOCAL_SAM_URL's (config/models.toml's default is localhost); SAM_HOST overrides the ssh target.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import time
from urllib.parse import urlsplit

import numpy as np
from PIL import Image

import calls

# the GPU host: SAM_HOST, else root@ the host of FIXIT_LOCAL_SAM_URL (else config's sam_url)
HOST = os.environ.get("SAM_HOST") or f"root@{urlsplit(calls.load_config().local_sam_url).hostname}"


def frame(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    im = np.full((480, 640, 3), 235, np.uint8)
    im[140:340, 220:420] = (rng.random((200, 200, 3)) * 80).astype(np.uint8)  # one dark square object
    buf = io.BytesIO()
    Image.fromarray(im).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def gpu_mib() -> str:
    q = ("pid=$(systemctl show -p MainPID --value fixit-sam); "
         "nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits"
         " | awk -F', ' -v p=$pid '$1==p{print $2}'")
    return subprocess.run(["ssh", HOST, q], capture_output=True, text=True, check=True).stdout.strip() or "0"


async def main() -> None:
    os.environ["FIXIT_LOCAL_SAM"] = "1"  # measured alone: Glimmer is not loaded beside it
    client = calls.make_client("live", local=True)
    for label, seed in (("cold", 1), ("warm", 2)):  # different frames: the client caches by frame hash
        t0 = time.perf_counter()
        # the cold call loads the model first: far past the 6 s a session allows, which is what this measures
        r = await client.sam_image(frame(seed), (330, 280, 670, 720), lane="l7",
                                   timeout_s=600 if label == "cold" else 30)
        print(f"{label}: {(time.perf_counter() - t0) * 1000:.0f} ms, {len(r.objects)} object(s)")
    print(f"fixit-sam holds {gpu_mib()} MiB on the 5090 (nvidia-smi, {time.strftime('%Y-%m-%d %H:%M')})")


if __name__ == "__main__":
    asyncio.run(main())
