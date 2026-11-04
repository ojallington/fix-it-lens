"""Writes fixit_lens.yaml, the ready dashboard for the demo home's appliances, from core.dashboard():

    uv run python integrations/homeassistant/dashboards/make.py

For your own home, Download diagnostics gives the same dashboard for your appliances (its `dashboard`)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "custom_components/fixit_lens/core.py"
DEMO = ["dishwasher", "washer", "boiler", "fridge", "oven"]  # deploy/demo_home.py's appliances
HEADER = ("# Fix-It Lens: a ready dashboard from Home Assistant's own cards (no custom card). make.py writes it\n"
          "# for the demo home's appliances; Download diagnostics gives it for yours. Paste it into a new\n"
          "# dashboard's raw configuration editor, or use it as a YAML-mode dashboard.\n")


class _Dumper(yaml.SafeDumper):
    """A template of several lines as a block (|), so it reads as written."""


_Dumper.add_representer(str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s
                                                               else None))


def render() -> str:
    spec = importlib.util.spec_from_file_location("fixit_lens_core", CORE)
    assert spec is not None and spec.loader is not None
    core = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("fixit_lens_core", core)
    spec.loader.exec_module(core)
    board = core.dashboard(DEMO, "Upkeep")
    return HEADER + yaml.dump(board, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


if __name__ == "__main__":
    (HERE / "fixit_lens.yaml").write_text(render())
