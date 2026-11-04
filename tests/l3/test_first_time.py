"""The A2 runner counts an add's cost by its run tag, not by what the lane spent meanwhile."""

from __future__ import annotations

from tools.first_time import run_usd


def test_cost_is_taken_from_the_run_tag_only() -> None:
    rows = [{"run": "card-build:aeg/LR7696UD4:f7c7b658849d", "usd": 0.86},
            {"run": "add-appliance:aeg/LR7696UD4:f7c7b658849d", "usd": 0.01},
            {"run": "card-build:acme/WM-0000:0123456789ab", "usd": 0.29},  # a fixture re-record meanwhile
            {"run": "card-build:aeg/LR7696UD4X:aaaaaaaaaaaa", "usd": 5.0},  # another model sharing a prefix
            {"run": None, "usd": 1.0}]
    assert round(run_usd(rows, "aeg", "LR7696UD4"), 2) == 0.87
