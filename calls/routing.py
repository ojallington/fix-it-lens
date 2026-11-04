"""Routing a public session's partner-role turns (guide and judge) to a free-credit pool, when config/models.toml
[partner] routing is on.

A session is pinned to one pool for the whole repair: mixing Spark and Spark Open within one repair would change how
the guide behaves. Two partner outages in a row, or a partner that can't be called at all (no key, switched off),
move the session to the next pool in order, and the call that moved it is re-run there. A public session never moves
to Meta's Spark: when no pool is left it raises PoolsExhausted, which the breaker does not count as an outage either,
so it never moves to local Glimmer.

    router = Router(pools, lambda name: calls.make_client("live", partner=name))
    client.router = router            # Client.respond sends public partner-role turns here
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from calls.client import Client, Response
from calls.errors import BackendUnavailable, BudgetExceeded, CallFailed, CallTimeout, ModelMismatch, PoolsExhausted
from calls.pools import Pools

TRIP_AFTER = 2
PINS = 4096
OUTAGE = (CallFailed, CallTimeout, BudgetExceeded, ModelMismatch)


class Router:
    def __init__(self, pools: Pools, client_for: Callable[[str], Client], *, trip_after: int = TRIP_AFTER) -> None:
        self.pools = pools
        self._client_for = client_for
        self._clients: dict[str, Client] = {}
        self.trip_after = trip_after
        self.pins: OrderedDict[str, str] = OrderedDict()  # session_id -> pool
        self.failures: dict[str, int] = {}
        self.moves: list[tuple[str, str, str]] = []  # (session_id, from pool, why), for the operator's view

    def client(self, pool: str) -> Client:
        if pool not in self._clients:
            self._clients[pool] = self._client_for(pool)
        return self._clients[pool]

    def _any_left(self, exclude: tuple[str, ...]) -> str | None:
        """For a session already running: any usable pool with credit left, floor or not."""
        left = self.pools.remaining()
        return next((n for n in self.pools.config.partner_order
                     if n not in exclude and self.pools.usable(n) and left.get(n, 0.0) > 0), None)

    def pool_for(self, origin: str, session_id: str | None) -> str:
        if session_id is not None and session_id in self.pins:
            self.pins.move_to_end(session_id)
            return self.pins[session_id]
        pool = self.pools.choose(origin) or self._any_left(())
        if pool is None:
            raise PoolsExhausted("every free-credit pool is used up or unavailable")
        if session_id is not None:
            self.pins[session_id] = pool
            while len(self.pins) > PINS:
                self.pins.popitem(last=False)
        return pool

    def _move(self, session_id: str | None, pool: str, tried: tuple[str, ...], why: str) -> str:
        nxt = self._any_left(tried)
        self.moves.append((session_id or "", pool, why[:200]))
        if nxt is None:
            raise PoolsExhausted(f"no free-credit pool left after {pool}: {why}"[:300])
        if session_id is not None:
            self.pins[session_id] = nxt
        self.failures[session_id or ""] = 0
        return nxt

    async def respond(self, role: str, input: Any, *, origin: str, session_id: str | None, **kw: Any) -> Response:
        pool = self.pool_for(origin, session_id)
        tried: tuple[str, ...] = ()
        key = session_id or ""
        while True:
            tried += (pool,)
            try:
                r = await self.client(pool).respond(role, input, origin=origin, session_id=session_id, **kw)
            except BackendUnavailable as e:
                pool = self._move(session_id, pool, tried, f"{type(e).__name__}: {e}")
                continue
            except OUTAGE as e:
                self.failures[key] = self.failures.get(key, 0) + 1
                if self.failures[key] < self.trip_after:
                    raise
                pool = self._move(session_id, pool, tried, f"{type(e).__name__}: {e}")
                continue
            self.failures[key] = 0
            return r
