"""Every way a model call can fail, as a type the caller can branch on."""

from __future__ import annotations


class CallError(Exception):
    """Base for every calls/ failure."""


class ConfigError(CallError):
    """config/models.toml is invalid, or names a refused model ID."""


class BudgetExceeded(CallError):
    """The ledger refused the call before any network I/O: the lane's budget or the total cap would be passed."""


class ModelMismatch(CallError):
    """response.model is not the pinned model ID."""


class IncompleteResponse(CallError):
    """The reply was incomplete twice (the retry at double the cap included). It is never parsed."""


class CallTimeout(CallError):
    """The call passed its role's timeout. usd is the ledger's estimate for it (a timed-out call is booked at that)."""

    usd: float = 0.0


class CallFailed(CallError):
    """The API returned an error or a failed response."""


class MissingFixture(CallError):
    """Replay mode found no recorded response for this request hash. Record it with FIXIT_CALLS=record."""


class BackendUnavailable(CallError):
    """The backend slot is not filled (local is for local mode; partner is unused unless the maintainer gets
    credits)."""


class PoolsExhausted(CallError):
    """A public session's turn found no free-credit pool left. It never falls back to paid or local models, and the
    breaker does not count it as an outage."""
