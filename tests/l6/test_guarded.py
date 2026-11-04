import json
from pathlib import Path

import pytest

from eval.stepcheck.guarded import resolve


def test_card_overrides_interim_flag_and_calls_cli_once() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        assert argv[1:4] == ["-m", "rules", "guarded"]
        return json.dumps({"filter.2": False, "filter.7": True})

    steps = [{"id": "dw.filter_out", "guarded": True, "card": "cards/bosch/X.yaml", "card_step": "filter.2"},
             {"id": "dw.seated_arrows", "guarded": False, "card": "cards/bosch/X.yaml", "card_step": "filter.7"},
             {"id": "wm.cover_open", "guarded": True}]
    assert resolve(steps, run) == {"dw.filter_out": False, "dw.seated_arrows": True, "wm.cover_open": True}
    assert len(calls) == 1


def test_unknown_card_step_fails() -> None:
    with pytest.raises(KeyError):
        resolve([{"id": "a", "guarded": False, "card": "c.yaml", "card_step": "zz"}], lambda argv: "{}")


def test_mapped_steps_hand_flag_matches_card() -> None:
    """Where steps.toml names a card step, the pre-registered guarded flag must equal fixit-rules' answer."""
    import tomllib

    steps = tomllib.loads((Path(__file__).resolve().parents[2] / "eval" / "steps.toml").read_text())["step"]
    mapped = [s for s in steps if "card" in s]
    got = resolve(mapped)
    assert {s["id"]: s["guarded"] for s in mapped} == got
