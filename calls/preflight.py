"""Preflight probes for /preflight: free, unledgered, 4 s, never raising.

    await calls.preflight_models()  # {"ok": bool, "detail": str, "missing": [model ids]}
    await calls.preflight_local()   # {"ok": bool, "detail": str}
    await calls.warm_up_local()     # {"ok": bool, "detail": str}: loads Glimmer; up to WARM_UP_S, run it on demand
    await calls.preflight_credits() # {"ok": bool, "detail": str}: free-credit routing, each pool's key and credit

GET /v1/models costs nothing and generates nothing, so it skips the ledger. The local probe is the local
backend's own `health(config)` (local mode): ollama answers and Glimmer is installed (it loads on its first call, or on
`warm_up_local`); SAM is reported when configured.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from calls.config import Config, load_config

TIMEOUT_S = 4.0
WARM_UP_S = 130.0  # outside the 4 s probe: local mode's warm_up waits up to 120 s itself


async def preflight_models(
    config: Config | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """Every pinned api model ID in config/models.toml is listed by GET /v1/models on the live base URL."""
    try:
        cfg = config or load_config()
        pinned = sorted({r.model for r in cfg.roles.values()})
        key = cfg.key_path.read_text(encoding="utf-8").strip()
        if not key:
            return {"ok": False, "detail": "the key file is empty", "missing": pinned}
        async with asyncio.timeout(TIMEOUT_S), httpx.AsyncClient(transport=transport, timeout=TIMEOUT_S) as http:
            r = await http.get(cfg.base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            return {"ok": False, "detail": f"GET /models: HTTP {r.status_code}", "missing": pinned}
        listed = {str(m.get("id")) for m in r.json().get("data", []) if isinstance(m, dict)}
        missing = [m for m in pinned if m not in listed]
        detail = f"{len(pinned) - len(missing)}/{len(pinned)} pinned models listed"
        return {"ok": not missing, "detail": detail + (f"; missing {', '.join(missing)}" if missing else ""),
                "missing": missing}
    except TimeoutError:
        return {"ok": False, "detail": f"GET /models: no answer in {TIMEOUT_S:g} s", "missing": []}
    except Exception as e:  # a preflight line is red, never an exception
        return {"ok": False, "detail": f"GET /models: {type(e).__name__}", "missing": []}


async def preflight_local(config: Config | None = None) -> dict[str, Any]:
    """The local backend answers and Glimmer is installed; SAM reported if configured (local mode's `health`)."""
    try:
        from calls.backends import local

        health = getattr(local, "health", None)
        if health is None:
            return {"ok": False, "detail": "no probe yet (local mode: calls.backends.local.health)"}
        async with asyncio.timeout(TIMEOUT_S):
            ok, detail = await health(config or load_config())
        return {"ok": bool(ok), "detail": str(detail)}
    except TimeoutError:
        return {"ok": False, "detail": f"local backend: no answer in {TIMEOUT_S:g} s"}
    except Exception as e:
        return {"ok": False, "detail": f"local backend: {type(e).__name__}"}


async def warm_up_local(config: Config | None = None) -> dict[str, Any]:
    """Load Glimmer (never SAM beside it) now rather than on the first call (local mode's `warm_up`). Never raises."""
    try:
        from calls.backends import local

        warm_up = getattr(local, "warm_up", None)
        if warm_up is None:
            return {"ok": False, "detail": "no warm-up yet (local mode: calls.backends.local.warm_up)"}
        async with asyncio.timeout(WARM_UP_S):
            ok, detail = await warm_up(config or load_config())
        return {"ok": bool(ok), "detail": str(detail)}
    except TimeoutError:
        return {"ok": False, "detail": f"still loading after {WARM_UP_S:g} s; check again in a minute"}
    except Exception as e:
        return {"ok": False, "detail": f"something went wrong ({type(e).__name__})"}


COOL_DOWN_S = 250.0  # local mode's cool_down waits up to 240 s for `engine down` to re-warm what was parked


async def cool_down_local(config: Config | None = None) -> dict[str, Any]:
    """After a live session: unload Glimmer and, through the GPU residency register, re-warm what the warm-up
    parked (local mode's `cool_down`). Never raises."""
    try:
        from calls.backends import local

        async with asyncio.timeout(COOL_DOWN_S):
            ok, detail = await local.cool_down(config or load_config())
        return {"ok": bool(ok), "detail": str(detail)}
    except TimeoutError:
        return {"ok": False, "detail": f"local cool-down: not done in {COOL_DOWN_S:g} s"}
    except Exception as e:
        return {"ok": False, "detail": f"local cool-down: {type(e).__name__}"}


PARTNER_NAMES = {"baseten": "Baseten", "fireworks": "Fireworks", "modal": "Modal"}


def _pool_word(cfg: Config, pools: Any, name: str, left: dict[str, float]) -> str:
    from calls.backends.partner import key_problem

    p = cfg.partners[name]
    shown = PARTNER_NAMES.get(name, name)
    if not p.enabled:
        return f"{shown}: off"
    if key_problem(p) is not None:
        state = "waits on key" if not p.key_path.exists() else "key file needs fixing"
    else:
        state = "ready"
    if name not in pools.credits.pools:
        return f"{shown}: {state}, no credit on file"
    floor = pools.credits.pools[name].floor
    return f"{shown}: {state}, ${left[name]:.2f} left (${floor:.2f} kept back)"


async def preflight_credits(config: Config | None = None, private: Any = None) -> dict[str, Any]:
    """Free-credit routing for public sessions: whether it is on, each partner's key and credit left against its
    floor, and Meta's public sub-budget. Reads the ledger and FIXIT_PRIVATE/credits.toml only. Never raises. With
    routing off nothing here is a fault: every session runs on Meta."""
    try:
        from calls import private_dir
        from calls.pools import pools_for

        cfg = config or load_config()
        pools = pools_for(cfg, private if private is not None else private_dir())
        spend = pools._spend()  # noqa: SLF001  # one ledger read for every number on the line
        left = pools.remaining(spend)
        words = [_pool_word(cfg, pools, n, left) for n in cfg.partner_order if n in cfg.partners]
        meta_left = pools.credits.meta_public_usd - spend.meta_public
        words.append(f"Meta for public sessions: ${meta_left:.2f} of ${pools.credits.meta_public_usd:.2f} left")
        if not cfg.routing:
            return {"ok": True, "detail": "Off: every session runs on Meta. " + " · ".join(words)}
        ok, _ = pools.open("judges")
        head = "On: a new public session fits." if ok else "On, but full: new public sessions are turned away."
        return {"ok": ok, "detail": head + " " + " · ".join(words)}
    except Exception as e:  # a preflight line is red, never an exception
        return {"ok": False, "detail": f"Could not read the credit figures ({type(e).__name__})"}
