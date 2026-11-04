"""Run with Home Assistant's test harness (not the repo's env):

    uv venv --python 3.13 .venv && uv pip install pytest-homeassistant-custom-component
    .venv/bin/pytest integrations/homeassistant/tests -p no:cacheprovider
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    return None
