"""What the session needs from the rest of the repo, as narrow interfaces.

- The rules (the `fixit-rules` package) are not behind an interface: the session imports `rules.SafetyEngine` directly,
  so there is nothing to swap out and no switch that skips them.
- `Manuals` is `ingest.manual_pages` and `ingest.check_quote`.
- `HomeAssistant` is the read-only `app/ha/` client.

Tests pass their own implementations of these to the session; production code never does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Page:
    page: int
    text: str
    ocr: bool = False


@dataclass(frozen=True)
class QuoteCheck:
    verified: bool
    page: int  # the page it was accepted on (or the cited page, when it was not)
    corrected_from: int | None = None
    reason: str | None = None


class Manuals(Protocol):
    def manual_pages(self, brand: str, model: str) -> list[Page]: ...  # with household=: that home's own copy first

    def verify(self, pages: list[Page], page: int, quote: str) -> QuoteCheck: ...


def pages_of(manuals: Manuals, brand: str, model: str, household: str | None) -> list[Page]:
    """The manual a card cites: a home's own card cites that home's copy, a shared or repo card the shared one."""
    if household is None:
        return manuals.manual_pages(brand, model)
    return manuals.manual_pages(brand, model, household=household)  # type: ignore[call-arg]


def manual_household(card_path: str | Path | None) -> str | None:
    """The home whose own card this file is (FIXIT_PRIVATE/cards/homes/<household>/...), else None."""
    from ingest.flow import household_of

    return household_of(Path(card_path)) if card_path else None


class HomeAssistant(Protocol):
    async def entities(self, alias: str) -> tuple[bool, list[dict[str, Any]]]:
        """(available, [{entity_id, state, unit, last_changed}]) for a household alias. Read-only."""
        ...

    async def power_w(self, alias: str) -> float | None:
        """The appliance's power entity in watts, or None when it has none."""
        ...

    async def power_state(self, alias: str) -> Any:
        """'on' | 'off' | 'unknown' from a binary power sensor, or None when it has none. Used when power_w is None."""
        ...


class NoHomeAssistant:
    """A household with no HA: every alias is unavailable and no appliance has a power entity."""

    async def entities(self, alias: str) -> tuple[bool, list[dict[str, Any]]]:
        return False, []

    async def power_w(self, alias: str) -> float | None:
        return None

    async def power_state(self, alias: str) -> Any:
        return None


class IngestManuals:
    """The ingest package: the manual's pages from the private folder, and the citation check."""

    def manual_pages(self, brand: str, model: str, household: str | None = None) -> list[Page]:
        from ingest import pages

        try:
            return [Page(p.number, p.text, p.ocr) for p in pages.manual_pages(brand, model, household)]
        except FileNotFoundError:
            return []

    def verify(self, pages: list[Page], page: int, quote: str) -> QuoteCheck:
        from ingest import citation

        r = citation.verify([p.text for p in pages], page, quote,
                            ocr_pages=frozenset(p.page for p in pages if p.ocr))
        return QuoteCheck(r.ok, r.page or page, r.corrected_from, None if r.ok else r.reason)
