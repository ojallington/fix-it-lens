from __future__ import annotations

from pathlib import Path

import pytest

from ingest import pages
from tools import make_acme_manual


@pytest.fixture(scope="session")
def acme_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_acme_manual.write(tmp_path_factory.mktemp("acme") / "acme-wm-0000.pdf")


@pytest.fixture(scope="session")
def acme_pages(acme_pdf: Path) -> list[str]:
    return [p.text for p in pages.extract(acme_pdf)]
