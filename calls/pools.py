"""Free-credit pools for public sessions (the open door and the judges' link): which pool a session runs on, and
whether one more session fits.

`pool_open(origin)` is the admission check. It is synchronous and cheap: it reads the ledger, the credits file and
whether each partner's key file is in place, never the network. It answers (True, None) to admit, or (False,
reopens_at) to close kindly, where reopens_at is UTC or None when no reopening is known (one-off credits do not
reset, so a pool closes with None).

A session is admitted only if a partner pool has room for a whole session above its floor, and Meta's public
sub-budget has room for that session's SAM and voice. The door stops at the floor plus a reserve kept for the
judges; the judges may run down to the floor. A session already running may spend into the floor, so nobody is cut
off mid-repair.

The credits file (FIXIT_PRIVATE/credits.toml) holds account facts, so it stays private:

    session_usd = 1.0            # the most one public session spends on a partner
    session_meta_usd = 0.1       # the most its SAM and voice spend on Meta
    judges_reserve_usd = 10.0    # the door closes this far above a pool's floor
    meta_public_usd = 20.0       # Meta's public sub-budget (the default when the line is absent)

    [pool.baseten]
    credit = 100.0
    floor = 5.0
    spent_outside = 0.0          # spend the ledger never saw, such as a dashboard test

A missing file means no partner credit: with routing on, every public session is closed. With routing off
(config/models.toml [partner]) public sessions run on Meta as every other session does, and all are admitted.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from calls.backends.partner import key_problem
from calls.config import PROVIDERS, Config
from calls.ledger import Ledger, Spend

Origin = Literal["door", "judges"]
ORIGINS = ("door", "judges")
META_PUBLIC_USD = 20.0


@dataclass(frozen=True)
class Pool:
    name: str
    credit: float
    floor: float = 0.0
    spent_outside: float = 0.0


@dataclass(frozen=True)
class Credits:
    pools: dict[str, Pool] = field(default_factory=dict)
    session_usd: float = 1.0
    session_meta_usd: float = 0.1
    judges_reserve_usd: float = 10.0
    meta_public_usd: float = META_PUBLIC_USD


def load_credits(path: Path | None) -> Credits:
    if path is None or not path.exists():
        return Credits()
    data = tomllib.loads(path.read_text())
    raw = data.get("pool", {})
    unknown = set(raw) - set(PROVIDERS)
    if unknown:
        raise ValueError(f"{path.name}: pools {sorted(unknown)} unknown; pools are {PROVIDERS}")
    pools = {name: Pool(name, float(p.get("credit", 0.0)), float(p.get("floor", 0.0)),
                        float(p.get("spent_outside", 0.0))) for name, p in raw.items()}
    return Credits(pools, float(data.get("session_usd", 1.0)), float(data.get("session_meta_usd", 0.1)),
                   float(data.get("judges_reserve_usd", 10.0)), float(data.get("meta_public_usd", META_PUBLIC_USD)))


def _check_origin(origin: str) -> None:
    if origin not in ORIGINS:
        raise ValueError(f"origin {origin!r}: only public sessions (door, judges) are admitted through pools")


class Pools:
    """The partner pools as they stand: what is left in each, and which one a new public session would take."""

    def __init__(self, config: Config, ledger: Ledger | None, credits: Credits) -> None:
        self.config = config
        self.ledger = ledger
        self.credits = credits

    def _spend(self) -> Spend:
        return self.ledger.spend() if self.ledger is not None else Spend({}, 0.0)

    def remaining(self, spend: Spend | None = None) -> dict[str, float]:
        """Credit left in each pool: credit, less spend outside the ledger, less the ledger's spend there."""
        s = spend or self._spend()
        return {n: p.credit - p.spent_outside - s.by_provider.get(n, 0.0) for n, p in self.credits.pools.items()}

    def usable(self, name: str) -> bool:
        """A pool can take a call: it has credit on file, and its partner is on with a key in place."""
        p = self.config.partners.get(name)
        return name in self.credits.pools and p is not None and key_problem(p) is None

    def choose(self, origin: str, *, exclude: tuple[str, ...] = (), spend: Spend | None = None) -> str | None:
        """The first pool in order with room for a whole new session above its floor (and, for the door, above
        the judges' reserve too), or None."""
        _check_origin(origin)
        left = self.remaining(spend)
        keep = self.credits.judges_reserve_usd if origin == "door" else 0.0
        for name in self.config.partner_order:
            if name in exclude or not self.usable(name):
                continue
            if left[name] - self.credits.pools[name].floor - keep >= self.credits.session_usd:
                return name
        return None

    def meta_room(self, spend: Spend | None = None) -> bool:
        """Meta's public sub-budget has room for one more public session's SAM and voice."""
        s = spend or self._spend()
        return self.credits.meta_public_usd - s.meta_public >= self.credits.session_meta_usd

    def open(self, origin: str) -> tuple[bool, datetime | None]:
        _check_origin(origin)
        s = self._spend()
        return (self.choose(origin, spend=s) is not None and self.meta_room(s)), None


def pools_for(config: Config, private: Path | None) -> Pools:
    ledger = Ledger(private / "ledger.jsonl", private / "budgets.toml") if private is not None else None
    return Pools(config, ledger, load_credits(private / "credits.toml" if private is not None else None))


def pool_open(origin: Origin) -> tuple[bool, datetime | None]:
    """Admit one more public session, or close kindly. With routing off every public session is admitted (it runs
    on Meta like any other)."""
    from calls import load_config, private_dir

    _check_origin(origin)
    cfg = load_config()
    if not cfg.routing:
        return True, None
    ok, reopens = pools_for(cfg, private_dir()).open(origin)
    return ok, reopens
