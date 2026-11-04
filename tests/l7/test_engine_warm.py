"""The warm-up goes through the GPU residency register when one is configured: `engine up` parks what the register
says and loads the model; ollama is not asked directly. A fake `engine` script stands in; no GPU, no network."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from calls import load_config
from calls.backends import local as backend

UP_OUT = ("muse-glimmer (ollama): needs 18980 MiB\n"
          "up: muse-glimmer on gpu-box in 21s; card 24704 used / 7903 free MiB; lease engine:muse-glimmer ttl 7200s\n"
          "parked: qwen3.8:27b, other-tts. `engine down muse-glimmer` re-warms them and releases the lease.\n")
DOWN_OUT = ("re-warmed: other-tts at 4654 MiB, 100% GPU\n"
            "down: muse-glimmer; card 10719 used / 21888 free MiB; lease released\n")


def fake_engine(tmp_path: Path, out: str, err: str = "", rc: int = 0) -> tuple[str, Path]:
    log = tmp_path / "argv"
    script = tmp_path / "engine"
    script.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\ncat <<'EOF'\n{out}EOF\ncat >&2 <<'EOF'\n{err}\nEOF\n"
                      f"exit {rc}\n")
    script.chmod(0o755)
    return str(script), log


def no_ollama() -> tuple[httpx.MockTransport, list[str]]:
    asked: list[str] = []

    def h(r: httpx.Request) -> httpx.Response:
        asked.append(r.url.path)
        return httpx.Response(200, json={"done": True, "load_duration": 1})
    return httpx.MockTransport(h), asked


def test_warm_up_calls_the_residency_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, log = fake_engine(tmp_path, UP_OUT)
    monkeypatch.setenv("FIXIT_ENGINE", engine)
    monkeypatch.setattr(backend, "_last_load_s", None)
    transport, asked = no_ollama()
    ok, detail = asyncio.run(backend.warm_up(load_config(), transport=transport))
    assert log.read_text().split() == ["up", "muse-glimmer", "--yes", "--ttl", str(backend.ENGINE_TTL_S)]
    assert asked == []  # the register loads it, not a bare ollama call
    # the page prefixes "Loaded: ", so the line does not say loaded again; GB, not the register's MiB
    assert ok and detail == ("Muse Glimmer 30B (local) in 21 s · paused to make room: qwen3.8:27b, other-tts · "
                             "8.3 GB of the GPU free")
    assert backend._last_load_s == 21.0


def test_a_refusal_from_the_plan_is_not_ok_and_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = fake_engine(tmp_path, "", "engine: refused lease-held: gpu:card is held by 'engine:x'", rc=3)
    monkeypatch.setenv("FIXIT_ENGINE", engine)
    transport, asked = no_ollama()
    ok, detail = asyncio.run(backend.warm_up(load_config(), transport=transport))
    assert not ok and asked == []
    assert detail == "Muse Glimmer 30B (local): the GPU is booked by another job (lease-held)"


def test_cool_down_runs_engine_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, log = fake_engine(tmp_path, DOWN_OUT)
    monkeypatch.setenv("FIXIT_ENGINE", engine)
    ok, detail = asyncio.run(backend.cool_down(load_config()))
    assert log.read_text().split() == ["down", "muse-glimmer"]
    assert ok and detail == "Muse Glimmer 30B (local): unloaded · what it paused is back on · 23.0 GB of the GPU free"


def test_an_unknown_refusal_keeps_the_registers_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = fake_engine(tmp_path, "", "engine: refused register-invalid: residency.toml: row x has no `mib`", rc=3)
    monkeypatch.setenv("FIXIT_ENGINE", engine)
    ok, detail = asyncio.run(backend.cool_down(load_config()))
    assert not ok and detail == ("Muse Glimmer 30B (local): the GPU plan said no "
                                 "(register-invalid: residency.toml: row x has no `mib`)")


def test_without_a_register_the_warm_up_is_a_bare_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIXIT_ENGINE", raising=False)
    transport, asked = no_ollama()
    ok, _ = asyncio.run(backend.warm_up(load_config(), transport=transport))
    assert ok and asked == ["/api/generate"]
    ok, detail = asyncio.run(backend.cool_down(load_config(), transport=transport))
    assert ok and detail.endswith("unloaded") and asked == ["/api/generate", "/api/generate"]


def test_cool_down_local_wraps_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import calls

    engine, _ = fake_engine(tmp_path, DOWN_OUT)
    monkeypatch.setenv("FIXIT_ENGINE", engine)
    assert asyncio.run(calls.cool_down_local())["ok"] is True
