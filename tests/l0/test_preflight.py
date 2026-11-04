"""calls.preflight_models / preflight_local: mocked transport, a temporary key, no network; never raising."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

import calls
from calls.backends import local
from calls.config import Config, load_config


def cfg(tmp_path: Path, key: str = "test-key-not-real") -> Config:
    (tmp_path / "k").write_text(key)
    return replace(load_config(), key_path=tmp_path / "k", base_url="https://models.invalid/v1")


def listing(ids: list[str], status: int = 200) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/models" and req.headers["authorization"] == "Bearer test-key-not-real"
        return httpx.Response(status, json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})

    return httpx.MockTransport(handler)


async def test_all_pinned_listed(tmp_path: Path) -> None:
    c = cfg(tmp_path)
    pinned = sorted({r.model for r in c.roles.values()})
    r = await calls.preflight_models(c, transport=listing([*pinned, "muse-spark-1.2"]))
    assert r == {"ok": True, "detail": f"{len(pinned)}/{len(pinned)} pinned models listed", "missing": []}


async def test_missing_model_is_red(tmp_path: Path) -> None:
    c = cfg(tmp_path)
    r = await calls.preflight_models(c, transport=listing(["muse-spark-1.3", "sam-3.1"]))
    assert r["ok"] is False and "muse-wonder-1.0" in r["missing"] and "missing" in r["detail"]


@pytest.mark.parametrize("transport", [
    listing([], status=401),
    httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down"))),
])
async def test_errors_are_red_not_raised(tmp_path: Path, transport: httpx.MockTransport) -> None:
    r = await calls.preflight_models(cfg(tmp_path), transport=transport)
    assert r["ok"] is False and r["detail"].startswith("GET /models")


async def test_no_key_file_is_red(tmp_path: Path) -> None:
    c = replace(load_config(), key_path=tmp_path / "absent")
    r = await calls.preflight_models(c, transport=listing([]))
    assert r["ok"] is False


async def test_local_without_probe_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(local, "health", raising=False)
    want = {"ok": False, "detail": "no probe yet (local mode: calls.backends.local.health)"}
    assert await calls.preflight_local() == want


async def test_local_health_ok_slow_and_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok(config: Any) -> tuple[bool, str]:
        return True, "glimmer loaded; sam warm"

    async def slow(config: Any) -> tuple[bool, str]:
        await asyncio.sleep(10)
        return True, ""

    async def broken(config: Any) -> tuple[bool, str]:
        raise OSError("no route")

    monkeypatch.setattr(local, "health", ok, raising=False)
    assert await calls.preflight_local() == {"ok": True, "detail": "glimmer loaded; sam warm"}
    monkeypatch.setattr("calls.preflight.TIMEOUT_S", 0.05)
    monkeypatch.setattr(local, "health", slow, raising=False)
    assert (await calls.preflight_local())["ok"] is False
    monkeypatch.setattr(local, "health", broken, raising=False)
    assert await calls.preflight_local() == {"ok": False, "detail": "local backend: OSError"}


async def test_warm_up_wraps_l7(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(local, "warm_up", raising=False)
    assert (await calls.warm_up_local())["detail"].startswith("no warm-up yet")

    async def ok(config: Any) -> tuple[bool, str]:
        return True, "glimmer loaded in 41 s"

    async def slow(config: Any) -> tuple[bool, str]:
        await asyncio.sleep(10)
        return True, ""

    monkeypatch.setattr(local, "warm_up", ok, raising=False)
    assert await calls.warm_up_local() == {"ok": True, "detail": "glimmer loaded in 41 s"}
    monkeypatch.setattr("calls.preflight.WARM_UP_S", 0.05)
    monkeypatch.setattr(local, "warm_up", slow, raising=False)
    assert (await calls.warm_up_local())["ok"] is False
