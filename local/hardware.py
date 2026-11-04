"""What local mode needs, measured on the GPU it ran on: the card, the model and its quantisation, the GPU memory the
model holds after real step checks, decode speed, the step check's latency, and the host's RAM. The smallest card it
would fit on is an estimate from the measured memory, labelled as such: it was not run on a smaller card.

    FIXIT_MODE=local FIXIT_GPU_SMI="ssh root@<gpu host>" python -m local.hardware measure   # live, Glimmer loaded
    python -m local.hardware score                                                          # claims, no network

`measure` asks ollama (FIXIT_LOCAL_URL) for the model's details and speed, runs the production step check
(app.stepcheck.check_clip, the local client, the no-SAM witness) on the demo replay's two public frames, and reads
nvidia-smi and /proc/meminfo on the GPU host through FIXIT_GPU_SMI, a command prefix such as an ssh to it. It writes
eval/results/local/hardware.json: numbers and names only, never a host name. Nothing it sends leaves the LAN.
`score` prints the eval row's claims from that file (n=0 before it exists).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shlex
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results" / "local" / "hardware.json"
FRAMES = ROOT / "demo" / "replays" / "demo" / "frames"
CARD = ROOT / "contracts" / "examples" / "card" / "acme-wm-0000.json"
STEP = "refit_filter"  # a step with a part: locate, judge and the no-SAM witness all run
CHECKS = 20
DECODE_RUNS = 5
DECODE_TOKENS = 256
CARD_SIZES_GB = (8, 12, 16, 20, 24, 32, 48, 80)
HEADROOM_GB = 1.0  # the CUDA context and a display, beside the model
GIB = 1024**3  # "GB" as card makers count it: a 32 GB card holds 32 GiB, so every GB here is a GiB
MIB_PER_GIB = 1024


def _gpu(cmd: str) -> list[str]:
    prefix = shlex.split(os.environ.get("FIXIT_GPU_SMI", ""))
    out = subprocess.run([*prefix, *shlex.split(cmd)], capture_output=True, text=True, timeout=30, check=True)
    return [x.strip() for x in out.stdout.splitlines() if x.strip()]


def read_gpu() -> dict[str, Any]:
    """The card, and the GPU memory each ollama or llama process holds on it (MiB)."""
    name, total = _gpu("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits")[0].split(", ")
    held = 0
    for line in _gpu("nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader,nounits"):
        proc, mib = line.rsplit(", ", 1)
        if "llama" in proc or "ollama" in proc:
            held += int(mib)
    mem = dict(x.split(":", 1) for x in _gpu("cat /proc/meminfo"))
    return {"gpu": name, "gpu_mib": int(total), "model_mib": held,
            "host_ram_gb": round(int(mem["MemTotal"].split()[0]) * 1024 / GIB, 1)}


async def read_model(base: str, model: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as http:
        show = (await http.post(base + "/api/show", json={"model": model})).raise_for_status().json()
        ps = (await http.get(base + "/api/ps")).raise_for_status().json()
        tags = (await http.get(base + "/api/tags")).raise_for_status().json()
    d = show.get("details", {})
    run = next((m for m in ps.get("models", []) if m.get("name") == model or m.get("model") == model), {})
    file = next((m for m in tags.get("models", []) if m.get("name") == model or m.get("model") == model), {})
    return {"model": model, "parameters": d.get("parameter_size"), "quant": d.get("quantization_level"),
            "weights_gb": round(int(file.get("size", 0)) / GIB, 1), "context": run.get("context_length"),
            "ollama_vram_gb": round(int(run.get("size_vram", 0)) / GIB, 1)}


async def decode_speed(base: str, model: str) -> list[float]:
    """Tokens per second while writing, as ollama times it (eval_count over eval_duration), thinking off."""
    out = []
    async with httpx.AsyncClient(timeout=120) as http:
        for i in range(DECODE_RUNS):
            body = {"model": model, "stream": False, "think": False,
                    "prompt": f"Write a numbered list of {40 + i} short safety tips for cleaning a washing machine "
                              "filter.", "options": {"num_predict": DECODE_TOKENS, "temperature": 0}}
            r = (await http.post(base + "/api/generate", json=body)).raise_for_status().json()
            out.append(round(r["eval_count"] / (r["eval_duration"] / 1e9), 1))
    return out


async def step_checks() -> list[float]:
    """Seconds per production step check in local mode (locate, judge, the no-SAM witness), wall clock."""
    import calls
    from app.stepcheck import check_clip
    from contracts.models.card import Card

    card = Card.model_validate_json(CARD.read_text())
    before, now = (FRAMES / "f_front.jpg").read_bytes(), (FRAMES / "f_casing.jpg").read_bytes()
    client = calls.make_client("live", local=True)
    out = []
    for _ in range(CHECKS):
        t0 = time.perf_counter()
        await check_clip(card, STEP, before, [(0.0, now), (0.5, now)], witness="track", lane="l7", client=client)
        out.append(round(time.perf_counter() - t0, 2))
    return out


def smallest_card_gb(model_gb: float) -> int:
    return next(s for s in CARD_SIZES_GB if s >= model_gb + HEADROOM_GB)


def measure() -> dict[str, Any]:
    from calls import load_config
    from calls.backends.local import _check_lan, _model

    if os.environ.get("FIXIT_MODE") != "local":
        raise SystemExit("measure: set FIXIT_MODE=local, so every call goes to the LAN")
    cfg = load_config()
    base, model = _check_lan(cfg.local_base_url).removesuffix("/v1"), _model(cfg)
    info = asyncio.run(read_model(base, model))
    tps = asyncio.run(decode_speed(base, model))
    checks = asyncio.run(step_checks())
    gpu = read_gpu()  # after real image calls, so the model's memory includes the image buffers
    out = {**gpu, **info, "decode_tps": tps, "step_check_s": checks, "step": STEP,
           "frames": ["f_front.jpg", "f_casing.jpg"], "measured_at": datetime.now(UTC).isoformat(timespec="seconds")}
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=1) + "\n")
    return out


def _p95(xs: list[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]


def claims(r: dict[str, Any] | None) -> list[dict[str, Any]]:
    if r is None:
        ids = ("gpu", "model", "vram_gb", "decode_tps", "step_check_p50", "step_check_p95", "host_ram_gb",
               "smallest_card_gb")
        return [{"id": f"local.hw.{i}", "value": None, "unit": "", "n": 0, "ci": None} for i in ids]
    model_gb = round(r["model_mib"] / MIB_PER_GIB, 1)
    on = f"measured on {r['gpu']}"
    return [
        {"id": "local.hw.gpu", "value": f"{r['gpu']}, {round(r['gpu_mib'] / MIB_PER_GIB)} GB", "unit": "GPU", "n": 1,
         "ci": None, "note": "the one card local mode was run on; it also holds other services"},
        {"id": "local.hw.model", "value": f"{r['model']} {r['quant']}", "unit": "model and quantisation", "n": 1,
         "ci": None, "note": f"{r['parameters']} parameters; weights {r['weights_gb']} GB; context {r['context']}"},
        {"id": "local.hw.vram_gb", "value": model_gb, "unit": "GB of GPU memory", "n": 1, "ci": None,
         "note": f"nvidia-smi, the model's processes, after {len(r['step_check_s'])} step checks with images; {on}"},
        {"id": "local.hw.decode_tps", "value": statistics.median(r["decode_tps"]), "unit": "tokens per second",
         "n": len(r["decode_tps"]), "ci": None,
         "note": f"median while writing, thinking off, {DECODE_TOKENS} tokens a run; {on}"},
        {"id": "local.hw.step_check_p50", "value": round(statistics.median(r["step_check_s"]), 1), "unit": "s",
         "n": len(r["step_check_s"]), "ci": None,
         "note": f"the production step check in local mode (locate, judge, no-SAM witness) on two public demo "
                 f"frames, verdict not scored; {on}"},
        {"id": "local.hw.step_check_p95", "value": round(_p95(r["step_check_s"]), 1), "unit": "s",
         "n": len(r["step_check_s"]), "ci": None, "note": on},
        {"id": "local.hw.host_ram_gb", "value": r["host_ram_gb"], "unit": "GB of RAM where the model ran", "n": 1,
         "ci": None, "note": "MemTotal where ollama runs: a container's memory limit can make it less than the "
                             "machine has; what local mode was given, not a measured minimum"},
        {"id": "local.hw.smallest_card_gb", "value": smallest_card_gb(model_gb), "unit": "GB card (estimated)",
         "n": 1, "ci": None,
         "note": f"estimated, not run: the measured {model_gb} GB plus {HEADROOM_GB:g} GB headroom, rounded up to a "
                 f"common card size, with the model alone on the card"},
    ]


def score() -> None:
    r = json.loads(RESULTS.read_text()) if RESULTS.exists() else None
    for c in claims(r):
        print(json.dumps(c, ensure_ascii=False))


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "measure":
        print(json.dumps(measure(), indent=1))
        return 0
    if cmd == "score":
        score()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
