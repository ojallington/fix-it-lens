"""local.hardware: claims from a measured run, and n=0 before one. No GPU, no network."""

from __future__ import annotations

from typing import Any

from local import hardware as hw

RUN: dict[str, Any] = {
    "gpu": "NVIDIA GeForce RTX 5090", "gpu_mib": 32607, "model_mib": 18980, "host_ram_gb": 134.9,
    "model": "muse-glimmer:30b", "parameters": "30.5B", "quant": "Q4_K_M", "weights_gb": 18.6, "context": 65536,
    "ollama_vram_gb": 19.9, "decode_tps": [150.1, 151.0, 149.2, 152.3, 150.6],
    "step_check_s": [1.2] * 18 + [1.9, 2.4], "step": "refit_filter", "frames": [], "measured_at": "x",
}


def test_claims_from_a_run() -> None:
    c = {x["id"]: x for x in hw.claims(RUN)}
    assert c["local.hw.gpu"]["value"] == "NVIDIA GeForce RTX 5090, 32 GB"
    assert c["local.hw.model"]["value"] == "muse-glimmer:30b Q4_K_M"
    assert c["local.hw.vram_gb"]["value"] == 18.5
    assert c["local.hw.decode_tps"]["value"] == 150.6 and c["local.hw.decode_tps"]["n"] == 5
    assert c["local.hw.step_check_p50"]["value"] == 1.2 and c["local.hw.step_check_p95"]["value"] == 1.9
    est = c["local.hw.smallest_card_gb"]
    assert est["value"] == 20 and "estimated" in est["unit"] and "not run" in est["note"]


def test_no_run_is_unmeasured(monkeypatch: Any, tmp_path: Any, capsys: Any) -> None:
    monkeypatch.setattr(hw, "RESULTS", tmp_path / "none.json")
    hw.score()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 8 and all('"n": 0' in x for x in lines)


def test_smallest_card_rounds_up_with_headroom() -> None:
    assert hw.smallest_card_gb(19.9) == 24 and hw.smallest_card_gb(23.5) == 32 and hw.smallest_card_gb(10.0) == 12
