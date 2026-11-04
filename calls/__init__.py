"""calls/: the only code that calls a model.

    import calls
    r = await calls.respond("judge", input, format=fmt, images=[now1, now2], lane="l1", household=h)
    m = await calls.sam_image(frame, (612, 700, 760, 880), lane="l1")
    t = await calls.transcribe(wav, ["Dutch"], ["pompfilter"], lane="l1")

The mode comes from FIXIT_CALLS: `replay` (the default; no key, fixtures only), `record` or `live`. The private
folder comes from FIXIT_PRIVATE. The key is read from the path in config/models.toml at call time, never from env.
FIXIT_MODE=local (or FIXIT_LOCAL=1) swaps the backend for local mode: Muse Glimmer and SAM 3.1 open weights on the
LAN, with the local role rows of config/models.toml (local mode). FIXIT_MODE=api is the default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from calls.backends.local import LocalBackend
from calls.backends.meta import MetaBackend
from calls.backends.partner import PartnerBackend
from calls.backends.replay import ReplayBackend
from calls.breaker import Breaker
from calls.client import (
    Box,
    Client,
    FunctionCall,
    Mode,
    Response,
    SamObject,
    SamResult,
    Transcript,
    Usage,
    run_tag,
)
from calls.config import ROOT, Config, load_config
from calls.errors import (
    BackendUnavailable,
    BudgetExceeded,
    CallError,
    CallFailed,
    CallTimeout,
    ConfigError,
    IncompleteResponse,
    MissingFixture,
    ModelMismatch,
    PoolsExhausted,
)
from calls.ledger import Ledger
from calls.pools import Origin, pool_open
from calls.preflight import cool_down_local, preflight_credits, preflight_local, preflight_models, warm_up_local

__all__ = [
    "BackendUnavailable", "Box", "Breaker", "BudgetExceeded", "CallError", "CallFailed", "CallTimeout", "Client",
    "Config",
    "ConfigError", "FunctionCall", "IncompleteResponse", "Ledger", "MissingFixture", "Mode", "ModelMismatch",
    "Response", "SamObject", "SamResult", "Transcript", "Usage", "default_client", "load_config", "local_mode",
    "make_client", "preflight_local", "preflight_models", "respond", "run_tag", "sam_image", "sam_video", "transcribe",
    "warm_up_local",
    "cool_down_local",
    "Origin", "pool_open", "PoolsExhausted", "preflight_credits",
]

FIXTURES = ROOT / "fixtures"
DEFAULT_PRIVATE = Path.home() / "Projects/hackathons/fix-it-lens-private"


def private_dir() -> Path | None:
    env = os.environ.get("FIXIT_PRIVATE")
    if env:
        return Path(env)
    return DEFAULT_PRIVATE if DEFAULT_PRIVATE.is_dir() else None


def make_client(
    mode: Mode = "replay",
    *,
    config: Config | None = None,
    private: Path | None = None,
    fixtures_root: Path = FIXTURES,
    fixture_roots: list[Path] | None = None,
    local: bool = False,
    partner: str | None = None,
) -> Client:
    """`partner` (baseten, fireworks, modal) gives the client for that free-credit pool: the partner roles on Spark
    Open there. It fails closed until the partner's key file is in place."""
    cfg = config or load_config()
    priv = private if private is not None else private_dir()
    ledger = Ledger(priv / "ledger.jsonl", priv / "budgets.toml") if priv is not None else None
    backend: Any
    if local and partner:
        raise ConfigError("a client is local or a partner's, not both")
    if local:
        cfg = cfg.local()  # the local role rows: replay of a local session keys on Glimmer's request bodies too
    if partner:
        cfg = cfg.partner(partner)
    if mode == "replay":
        backend = ReplayBackend(fixture_roots or [fixtures_root])
        ledger = None  # replay spends nothing
    elif local:
        backend = LocalBackend(cfg.local_base_url, cfg.local_sam_url if cfg.local_sam_on else "")
    elif partner:
        backend = PartnerBackend(cfg.partners[partner])
    else:
        backend = MetaBackend(cfg.base_url, cfg.key_path)
    client = Client(mode, cfg, backend, ledger=ledger, fixtures_root=fixtures_root, private=priv)
    if cfg.routing and not local and not partner:
        from calls.pools import pools_for
        from calls.routing import Router

        client.router = Router(pools_for(cfg, priv), lambda name: make_client(
            mode, config=config, private=priv, fixtures_root=fixtures_root, fixture_roots=fixture_roots,
            partner=name))
    return client


_default: Client | None = None


def local_mode() -> bool:
    fm = os.environ.get("FIXIT_MODE", "api")
    if fm not in ("api", "local"):
        raise ConfigError(f"FIXIT_MODE={fm!r}; use api or local")
    return fm == "local" or os.environ.get("FIXIT_LOCAL") == "1"


def default_client() -> Client:
    global _default
    if _default is None:
        mode = os.environ.get("FIXIT_CALLS", "replay")
        if mode not in ("live", "record", "replay"):
            raise ConfigError(f"FIXIT_CALLS={mode!r}; use live, record or replay")
        _default = make_client(mode, local=local_mode())  # type: ignore[arg-type]
    return _default


async def respond(role: str, input: Any, **kw: Any) -> Response:
    return await default_client().respond(role, input, **kw)


async def sam_image(frame: bytes, prompt: str | Box, **kw: Any) -> SamResult:
    return await default_client().sam_image(frame, prompt, **kw)


async def sam_video(clip: bytes, prompt: str | Box, **kw: Any) -> SamResult:
    return await default_client().sam_video(clip, prompt, **kw)


async def transcribe(
    audio: bytes, language_bias: Any = ("Dutch", "English"), keywords: Any = (), **kw: Any
) -> Transcript:
    return await default_client().transcribe(audio, language_bias, keywords, **kw)
