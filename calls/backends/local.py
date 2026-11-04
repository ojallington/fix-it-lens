"""The local backend (FIXIT_MODE=local): Muse Glimmer through ollama's OpenAI door, SAM 3.1 open weights through
local/sam_server. Nothing it sends leaves the LAN: a base URL on a public host is refused at construction.

The client still builds Responses bodies and reads Responses events; calls.backends.chat translates both ways, so the
loop, the ledger, record and replay are the same in both modes.

Glimmer settings, measured on an RTX 5090 under ollama 0.34.0:
- `reasoning_effort: "none"` on the OpenAI door turns thinking off (15 completion tokens for a 15-token answer).
  ollama's `think: false` is ignored on this door; with thinking on, checks take 3-9 s and score lower.
- ollama leaks the `<|eot|>` stop token into the content, with and without a JSON schema. It is stripped here,
  or every JSON parse fails.
- cold load into VRAM: 17.4 s (ollama load_duration; Q4_K_M at a 65536 context holds 18.6 GB), which is what
  /preflight's warm-up spares the first call.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shlex
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from calls.backends.chat import ResponsesTranslator, chat_body, strip_stop_tokens  # noqa: F401  # re-exported
from calls.backends.chat import sse as _sse
from calls.errors import BackendUnavailable, CallFailed, ConfigError

LAN_SUFFIXES = (".local", ".lan", ".home.arpa", ".ts.net", ".internal")
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def is_lan_host(host: str) -> bool:
    """A literal private, loopback or tailnet address, or a LAN-style name (one label, or a LAN suffix)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(LAN_SUFFIXES)
    return ip.is_private or ip.is_loopback or ip in TAILNET


def _check_lan(url: str) -> str:
    host = urlsplit(url).hostname or ""
    if not is_lan_host(host):
        raise ConfigError(f"local backend URL {url!r} is not on the LAN; local mode sends nothing off it")
    return url.rstrip("/")


class LocalBackend:
    name = "local"

    def __init__(self, base_url: str, sam_url: str = "", *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = _check_lan(base_url)
        self.sam_url = _check_lan(sam_url) if sam_url else ""
        self.transport = transport  # tests pass a recorded transport; nothing else does

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=None, transport=self.transport)

    async def stream(
        self, body: dict[str, Any], *, sha: str, kind: str
    ) -> AsyncIterator[tuple[float, dict[str, Any]]]:
        if kind == "respond":
            async for ev in self._glimmer(body):
                yield ev
        else:
            async for ev in self._sam(body):
                yield ev

    async def _glimmer(self, body: dict[str, Any]) -> AsyncIterator[tuple[float, dict[str, Any]]]:
        t0 = time.perf_counter()
        tr = ResponsesTranslator()
        try:
            async with self._http() as http, http.stream("POST", self.base_url + "/chat/completions",
                                                         json=chat_body(body)) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    yield (time.perf_counter() - t0) * 1000, {"type": "error", "code": f"http_{r.status_code}",
                                                              "message": detail}
                    return
                async for chunk in _sse(r):
                    for ev in tr.feed(chunk):
                        yield (time.perf_counter() - t0) * 1000, ev
        except httpx.TransportError as e:
            raise CallFailed(f"local Glimmer at {self.base_url} unreachable: {type(e).__name__}") from None
        yield (time.perf_counter() - t0) * 1000, tr.final()

    async def _sam(self, body: dict[str, Any]) -> AsyncIterator[tuple[float, dict[str, Any]]]:
        """local/sam_server speaks the hosted API's Responses SSE, so the same parser reads both."""
        if not self.sam_url:
            raise BackendUnavailable("no local SAM server configured; local mode outlines from Glimmer's boxes")
        t0 = time.perf_counter()
        try:
            async with self._http() as http, http.stream("POST", self.sam_url + "/v1/responses", json=body) as r:
                if r.status_code != 200:
                    raise CallFailed(f"local SAM: HTTP {r.status_code}")
                async for ev in _sse(r):
                    yield (time.perf_counter() - t0) * 1000, ev
        except httpx.TransportError as e:
            raise BackendUnavailable(f"local SAM at {self.sam_url} unreachable: {type(e).__name__}") from None

    async def transcribe(
        self, request: dict[str, Any], wav: bytes, *, sha: str
    ) -> tuple[float, int, dict[str, Any]]:
        raise BackendUnavailable("speech-to-text has no local Meta model; local mode uses taps and typed input")

    async def count_tokens(self, body: dict[str, Any], *, sha: str) -> tuple[float, int, dict[str, Any]]:
        """ollama has no count endpoint: a one-token completion reports Glimmer's own prompt_tokens (one prefill,
        no money). The local runner's slot is 64k on the 5090, so this is what the guide checks its context by."""
        req = {**chat_body({**body, "max_output_tokens": 1}), "stream": False}
        req.pop("stream_options")
        t0 = time.perf_counter()
        try:
            async with self._http() as http:
                r = await http.post(self.base_url + "/chat/completions", json=req)
        except httpx.TransportError as e:
            raise CallFailed(f"local Glimmer at {self.base_url} unreachable: {type(e).__name__}") from None
        latency = (time.perf_counter() - t0) * 1000
        try:
            data = r.json()
        except ValueError:
            return latency, r.status_code, {"error": r.text[:300]}
        tokens = (data.get("usage") or {}).get("prompt_tokens")
        return latency, r.status_code, {"input_tokens": tokens} if r.status_code == 200 else data


# Where local/sam_server/fetch.py reads the token and puts the weights (stat only: the token is never opened here).
HF_TOKEN = Path.home() / ".config" / "fleet" / "hf-read.token"
SAM_WEIGHTS = Path(os.environ.get("SAM_WEIGHTS", Path.home() / ".cache" / "fixit" / "sam3.1"))


def sam_weights_pending(token: Path | None = None, weights: Path | None = None) -> bool:
    """True while the facebook/sam3.1 gate has not given us weights: no read token, or nothing in the weights dir."""
    token, weights = token or HF_TOKEN, weights or SAM_WEIGHTS
    try:
        return not token.exists() or not weights.is_dir() or not any(weights.iterdir())
    except OSError:
        return True


NO_SAM_WITNESS = "Second check: Glimmer finds the part and motion tracking follows its place (no SAM)"
# SAM 3.1 (11.2 GB after a clip) and Glimmer do not fit on one 32 GB GPU at once: local SAM is for eval or a
# configuration without Glimmer (FIXIT_LOCAL_SAM=1), never beside it.
SAM_OFF = "Local SAM 3.1: on demand, not used beside Glimmer"


_last_load_s: float | None = None  # ollama's load_duration from the last warm_up in this process


DETAIL_SEP = " · "


def _name(model: str) -> str:
    """How /preflight names the local model: "Muse Glimmer 30B (local)" for muse-glimmer:30b."""
    family, _, size = model.partition(":")
    return f"{family.replace('-', ' ').title()} {size.upper()} (local)".replace("  ", " ")


def _model(config: Any) -> str:
    return str(config.local_roles["guide"].model) if "guide" in config.local_roles else "muse-glimmer:30b"


async def health(config: Any, *, transport: httpx.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """For /preflight, never raises. ok = ollama answers and Glimmer is installed (/api/tags); ollama loads it on
    the first call and the 5090 is shared, so not being in memory (/api/ps) is reported, not failed. SAM, when
    sam_url is set, is its own part of the detail, since local mode has a witness without it; unless FIXIT_LOCAL_SAM=1
    it is SAM_OFF and the SAM server is not asked."""
    model = _model(config)
    async with httpx.AsyncClient(timeout=3.0, transport=transport) as http:
        try:
            base = _check_lan(config.local_base_url).removesuffix("/v1")
            tags = (await http.get(base + "/api/tags")).raise_for_status().json()
            ok = any(model in (m.get("name"), m.get("model")) for m in tags.get("models", []))
            if not ok:
                parts = [f"{_name(model)}: not installed"]
            else:
                ps = (await http.get(base + "/api/ps")).raise_for_status().json()
                if any(model in (m.get("name"), m.get("model")) for m in ps.get("models", [])):
                    parts = [f"{_name(model)}: loaded and ready"]
                else:
                    took = f" (~{_last_load_s:.0f} s)" if _last_load_s is not None else ""
                    parts = [f"{_name(model)}: installed, loads on first use{took}"]
        except (httpx.HTTPError, ValueError, ConfigError) as e:
            ok, parts = False, [f"{_name(model)}: can't be reached ({type(e).__name__})"]
        if not ok and not config.local_sam_on:
            return ok, parts[0]  # Glimmer is the reason; SAM's notes and its witness hang on Glimmer
        if config.local_sam_url and not config.local_sam_on:
            parts.append(SAM_OFF)
            parts.append(NO_SAM_WITNESS)
        elif config.local_sam_url and sam_weights_pending():
            parts += ["Local SAM 3.1: no weights yet (facebook/sam3.1 access pending)", NO_SAM_WITNESS]
        elif config.local_sam_url:
            try:
                sam = (await http.get(_check_lan(config.local_sam_url) + "/health")).raise_for_status().json()
                if sam.get("loaded"):
                    parts.append("Local SAM 3.1: loaded and ready")
                elif sam.get("weights"):
                    parts.append("Local SAM 3.1: ready, loads on first use")
                else:
                    parts += ["Local SAM 3.1: no weights yet", NO_SAM_WITNESS]
            except (httpx.HTTPError, ValueError, ConfigError) as e:
                parts += [f"Local SAM 3.1: can't be reached ({type(e).__name__})", NO_SAM_WITNESS]
    return ok, DETAIL_SEP.join(parts)


# Where a GPU residency register decides what fits on the card, the warm-up loads through it: FIXIT_ENGINE is its
# `engine` command, and `engine up ROW --yes` takes the card's lease, parks what the row declares, loads the model and
# checks it is 100 % on the GPU. Without it (one GPU, nothing else on it) the warm-up is a bare ollama load.
ENGINE_ROW = os.environ.get("FIXIT_ENGINE_ROW", "muse-glimmer")
ENGINE_TTL_S = int(os.environ.get("FIXIT_ENGINE_TTL_S", "7200"))  # the lease, so a session is not cut off
_UP = re.compile(r"^up: \S+ on \S+ in (?P<s>\d+)s; card (?P<used>\d+) used / (?P<free>\d+) free MiB", re.M)
_DOWN = re.compile(r"^down: .*card (?P<used>\d+) used / (?P<free>\d+) free MiB", re.M)
_PARKED = re.compile(r"^parked: (?P<what>.*?)\. ", re.M)
_REFUSED = re.compile(r"engine: refused (?P<code>[\w-]+): (?P<why>.*)")


async def _engine(engine: str, *args: str, timeout_s: float) -> tuple[int, str, str]:
    """Run the register's `engine` verb. On a timeout it is left to finish: killing it mid-`up` would leave the
    lease taken and the parked services down, which its own error path otherwise undoes."""
    proc = await asyncio.create_subprocess_exec(*shlex.split(engine), *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        return 124, "", f"still busy after {timeout_s:.0f} s; it finishes by itself"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


# The register's refusal codes an operator meets, in plain words; any other code keeps the register's own why.
_PLAIN_REFUSAL = {
    "lease-held": "the GPU is booked by another job",
    "card-busy": "not enough free memory on the GPU",
    "host-unreachable": "the GPU computer doesn't answer",
    "headset-held": "the GPU is in use and can't be freed now",
    "hold-unknown": "the GPU is in use and can't be freed now",
}


def _gb(mib: str) -> str:
    return f"{int(mib) * 1.048576 / 1000:.1f} GB"


def _refused(name: str, rc: int, out: str, err: str, verb: str = "load") -> str:
    """Why the register said no, as the operator reads it after "Not loaded: " (the page's prefix)."""
    m = _REFUSED.search(err)
    if m:
        plain = _PLAIN_REFUSAL.get(m["code"])
        return f"{name}: {plain} ({m['code']})" if plain else \
            f"{name}: the GPU plan said no ({m['code']}: {m['why'][:160]})"
    if rc == 124:
        return f"{name}: {err}"
    return f"{name}: couldn't {verb} (engine exit {rc})"


async def _warm_through_engine(engine: str, name: str, timeout_s: float) -> tuple[bool, str]:
    global _last_load_s
    rc, out, err = await _engine(engine, "up", ENGINE_ROW, "--yes", "--ttl", str(ENGINE_TTL_S), timeout_s=timeout_s)
    m = _UP.search(out)
    if rc != 0 or m is None:
        return False, _refused(name, rc, out, err)
    _last_load_s = float(m["s"])
    parked = _PARKED.search(out)
    what = f"{DETAIL_SEP}paused to make room: {parked['what']}" if parked and parked["what"] != "nothing" else ""
    return True, f"{name} in {m['s']} s{what}{DETAIL_SEP}{_gb(m['free'])} of the GPU free"


async def cool_down(config: Any, *, transport: httpx.AsyncBaseTransport | None = None,
                    timeout_s: float = 240.0) -> tuple[bool, str]:
    """After a live session: unload Glimmer. Through the register, `engine down` also re-warms what the warm-up
    parked and releases the card's lease. Never raises."""
    name = _name(_model(config))
    engine = os.environ.get("FIXIT_ENGINE", "")
    if engine:
        rc, out, err = await _engine(engine, "down", ENGINE_ROW, timeout_s=timeout_s)
        m = _DOWN.search(out)
        if rc != 0 or m is None:
            return False, _refused(name, rc, out, err, "unload")
        back = f"{DETAIL_SEP}what it paused is back on"
        return True, f"{name}: unloaded{back}{DETAIL_SEP}{_gb(m['free'])} of the GPU free"
    try:
        base = _check_lan(config.local_base_url).removesuffix("/v1")
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as http:
            body = {"model": _model(config), "prompt": "", "keep_alive": 0}
            (await http.post(base + "/api/generate", json=body)).raise_for_status()
    except (httpx.HTTPError, ValueError, ConfigError) as e:
        return False, f"{name}: couldn't unload ({type(e).__name__})"
    return True, f"{name}: unloaded"


async def warm_up(config: Any, *, transport: httpx.AsyncBaseTransport | None = None,
                  timeout_s: float = 120.0) -> tuple[bool, str]:
    """Before a live demo: load Glimmer onto the GPU now (an empty /api/generate, nothing generated), so the first
    real calls are warm. Never SAM: it does not fit beside Glimmer, and the api mode's SAM is hosted. With
    FIXIT_ENGINE set, the load goes through the GPU residency register (`engine up`), which parks what it must: the
    register, not this button, says what fits. Never raises."""
    global _last_load_s
    model = _model(config)
    engine = os.environ.get("FIXIT_ENGINE", "")
    if engine:
        return await _warm_through_engine(engine, _name(model), timeout_s)
    try:
        base = _check_lan(config.local_base_url).removesuffix("/v1")
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as http:
            t0 = time.perf_counter()
            r = (await http.post(base + "/api/generate", json={"model": model, "prompt": ""})).raise_for_status()
            wall = time.perf_counter() - t0
        load_ns = r.json().get("load_duration")
        load_s = load_ns / 1e9 if isinstance(load_ns, int | float) else wall
    except (httpx.HTTPError, ValueError, ConfigError) as e:
        return False, f"{_name(model)}: couldn't load ({type(e).__name__})"
    _last_load_s = load_s
    return True, f"{_name(model)} in {load_s:.1f} s"
