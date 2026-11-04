"""The household REST routes (contracts/api.openapi.yaml), as one APIRouter that app/server.py mounts under /api/v1:

    app.include_router(make_router(deps), prefix="/api/v1")

The pilot door (invite code -> household -> token), the schedule (/due, the "last done?" answer), the feeds (.ics,
push) and reminder opens. Every household route reads only that household's rows.
"""


import asyncio
import sqlite3
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from jsonschema import ValidationError
from pydantic import BaseModel, Field, field_validator

from app import erase
from app.feeds import feedback, ics, push
from app.feeds.preflight import Check, Line, no_cert, run_checks
from app.schedule import door, manage, people, store
from app.schedule.dates import household_tz, local_today
from app.schedule.due import CardLoader, Due, Schedule, checked, due
from contracts.models.feedback import Answers

PublicOrigin = Literal["door", "judges"]


def _pool_open(origin: PublicOrigin) -> tuple[bool, datetime | None]:
    import calls  # the credit pools (calls/pools.py); with routing off every public session is admitted

    return calls.pool_open(origin)


@dataclass
class Deps:
    conn: sqlite3.Connection
    secret: bytes
    load_card: CardLoader
    vapid: push.Vapid | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    # Bad invite codes allowed per client address per hour, before 429.
    invite_failures_per_hour: int = 10
    # /preflight's checks for the certificate the request came in on (host, port); None: no /preflight lines.
    preflight: Callable[[list[tuple[str, int]]], list[tuple[str, Check]]] | None = None
    # Loads the local model (calls.warm_up_local: up to ~2 min, never raises); None: no warm-up here.
    warm_up: Callable[[], Awaitable[dict[str, Any]]] | None = None
    # After a finished warm-up the route answers 409 for this long, so an open POST cannot keep the GPU busy.
    warm_up_cooldown: timedelta = timedelta(seconds=60)
    # Whether a public session may start (the open door, a judges' link): (ok, reopens_at). calls.pool_open.
    pool_open: Callable[[PublicOrigin], tuple[bool, datetime | None]] = _pool_open
    # New homes through the open door per UTC day: from one client address, and in all. Then 429 until midnight UTC.
    door_homes_per_address_day: int = 3
    door_homes_per_day: int = 50
    # What these households have spent, ever (caps.households_spend over the ledger); None: no ledger, nothing spent.
    spend: Callable[[tuple[str, ...]], float] | None = None
    # FIXIT_PRIVATE, where app/feeds/feedback.py keeps the answers; None: this server takes no feedback (503).
    private: Path | None = None


class HouseholdIn(BaseModel):
    invite_code: str | None = Field(default=None, max_length=40)   # none: the open door, which needs age_ok
    consent: Literal[True]
    age_ok: Literal[True] | None = None
    country: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    lang: str | None = Field(default=None, pattern=r"^[a-z]{2}$")


class TryIn(BaseModel):
    model_config = {"extra": "forbid"}
    code: str = Field(min_length=1, max_length=100)
    consent: Literal[True]
    age_ok: Literal[True]
    country: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    lang: str | None = Field(default=None, pattern=r"^[a-z]{2}$")


class PersonPatchIn(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=40)
    role: Literal["admin", "member"] | None = None
    view_only: bool | None = None


class InviteIn(BaseModel):
    model_config = {"extra": "forbid"}
    role: Literal["admin", "member"]
    view_only: bool = False
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    expires_in_days: int = Field(default=7, ge=1, le=14)


class JoinIn(BaseModel):
    model_config = {"extra": "forbid"}
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=40)


class QuietIn(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}
    from_: str = Field(alias="from", pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    to: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class AlertsIn(BaseModel):
    model_config = {"extra": "forbid"}
    push: bool
    calendar: bool
    hub: bool
    items: Literal["all", "chosen"]
    follows: list[str] = Field(max_length=200)
    quiet: QuietIn | None


class AssigneeIn(BaseModel):
    model_config = {"extra": "forbid"}
    person_id: str | None


PEOPLE_STATUS = {"not_found": 404, "forbidden": 403, "invalid": 422, "last_admin": 409, "name_taken": 409}


def _people_error(e: people.PeopleError) -> HTTPException:
    return HTTPException(PEOPLE_STATUS[e.error], {"error": e.error, "detail": e.detail})


class ReturnIn(BaseModel):
    model_config = {"extra": "forbid"}
    code: str = Field(min_length=1, max_length=100)


def _next_utc_midnight(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date() + timedelta(days=1), datetime.min.time(), UTC)


def _cap_error(error: str, reopens_at: datetime | None, detail: str) -> HTTPException:
    """A 429 with the contract's CapError body."""
    at = None if reopens_at is None else reopens_at.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z")
    return HTTPException(429, {"error": error, "reopens_at": at, "detail": detail})


class LastDoneIn(BaseModel):
    on: date | None = Field(default=None, alias="date")
    source: Literal["user", "never", "unknown"]


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=1000)
    expirationTime: float | None = None  # noqa: N815 (the browser's field name)
    keys: PushKeys


class EndpointIn(BaseModel):
    endpoint: str


class OpenedIn(BaseModel):
    channel: Literal["push", "ics", "caldav", "ha", "app"]


class SnoozeIn(BaseModel):
    model_config = {"extra": "forbid"}
    hours: int = Field(24, ge=1, le=72)


class DoneIn(BaseModel):
    model_config = {"extra": "forbid"}
    on: date | None = Field(default=None, alias="date")


class UndoIn(BaseModel):
    model_config = {"extra": "forbid"}
    own: bool = False


class JobSnoozeIn(BaseModel):
    model_config = {"extra": "forbid"}
    until: date | None


class ScheduleIn(BaseModel):
    """{"kind": "every", "every", "unit"} | {"kind": "window", "min", "max", "unit"} | {"kind": "manual"}"""
    model_config = {"extra": "forbid"}
    kind: Literal["every", "window", "manual"]
    every: int | None = Field(default=None, ge=1, le=1000)
    min: int | None = Field(default=None, ge=1, le=1000)
    max: int | None = Field(default=None, ge=2, le=1000)
    unit: Literal["day", "week", "month", "year"] | None = None

    def schedule(self) -> Schedule | None:
        if self.kind == "manual":
            if self.every or self.min or self.max or self.unit:
                raise ValueError("manual takes no interval")
            return None
        if self.unit is None:
            raise ValueError("a schedule needs a unit")
        if self.kind == "every":
            if self.every is None or self.min or self.max:
                raise ValueError("every takes every and unit")
            return Schedule("every", self.unit, every=self.every)
        if self.min is None or self.max is None or self.every:
            raise ValueError("a window takes min, max and unit")
        if self.min >= self.max:
            raise ValueError("a window needs min < max")
        return Schedule("window", self.unit, min=self.min, max=self.max)


class TokenIn(BaseModel):
    model_config = {"extra": "forbid"}
    by_kind: Literal["agent", "ha"]
    scope: Literal["read", "write"]
    label: str = Field(min_length=1, max_length=60)

    @field_validator("by_kind", mode="before")
    @classmethod
    def _no_feed(cls, v: object) -> object:
        if v == "feed":
            raise ValueError("a calendar address is not a token: it comes from GET /household/feeds")
        return v


class FeedbackIn(BaseModel):
    """One filled feedback form (contracts/feedback.schema.json without what the server sets)."""
    model_config = {"extra": "forbid"}
    lang: Literal["nl", "en"] | None
    answers: Answers
    quote: Literal["none", "anonymous", "named"]
    quote_name: str | None = Field(default=None, min_length=1, max_length=2000)


class HouseholdPatchIn(BaseModel):
    model_config = {"extra": "forbid"}
    renter: bool | None


def _household_json(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """The contract's Household. renter is null until someone in the home says whether they rent; pilot is whether it
    is in the store's pilot_cohort view, the homes asked for feedback."""
    keys = row.keys()
    renter = row["renter"] if "renter" in keys else None
    pilot = conn.execute("SELECT 1 FROM pilot_cohort WHERE id = ?", (row["id"],)).fetchone() is not None
    return {"id": row["id"], "country": row["country"], "lang": row["lang"], "cap_usd_day": row["cap_usd_day"],
            "name": row["name"] if "name" in keys else None, "renter": None if renter is None else bool(renter),
            "pilot": pilot}


def make_router(deps: Deps) -> APIRouter:
    r = APIRouter()
    failures: dict[str, deque[float]] = defaultdict(deque)
    warming = asyncio.Lock()
    warm_again_at: list[datetime] = []

    def caller(authorization: Annotated[str | None, Header()] = None) -> store.Caller:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "household token required")
        c = store.caller_from_token(deps.conn, deps.secret, authorization[7:].strip(), at=deps.clock())
        if c is None:
            raise HTTPException(401, "bad household token")
        return c

    Caller = Annotated[store.Caller, Depends(caller)]

    def household(c: Caller) -> sqlite3.Row:
        return c.household

    def writer(c: Caller) -> store.Caller:
        if c.scope != "write" or c.by_kind == "feed":
            raise HTTPException(403, "this token is read-only")
        if c.by_kind == "app" and c.person_id is not None:
            p = people.person(deps.conn, c.household_id, c.person_id)
            if p is None or p["view_only"]:
                raise HTTPException(403, "this person can view but not change anything")
        return c

    def me(c: Caller) -> tuple[store.Caller, sqlite3.Row]:
        """A person's own token (the household's, for its first admin, or their app token), and the person."""
        p = people.person(deps.conn, c.household_id, c.person_id) if c.by_kind == "app" and c.person_id else None
        if p is None:
            raise HTTPException(403, "only a person's own token does this")
        return c, p

    def app_only(c: Caller) -> store.Caller:
        if c.by_kind != "app" or not store.is_admin(deps.conn, c.person_id):
            raise HTTPException(403, "only the home's admin can do this")
        return c

    Household = Annotated[sqlite3.Row, Depends(household)]
    Writer = Annotated[store.Caller, Depends(writer)]
    AppOnly = Annotated[store.Caller, Depends(app_only)]
    Me = Annotated[tuple[store.Caller, sqlite3.Row], Depends(me)]

    def throttle(request: Request) -> str:
        who = request.client.host if request.client else "?"
        q = failures[who]
        while q and q[0] < time.monotonic() - 3600:
            q.popleft()
        if len(q) >= deps.invite_failures_per_hour:
            raise HTTPException(429, "too many bad codes; try again later")
        return who

    def created(hid: str, token: str | None = None) -> dict[str, Any]:
        row = deps.conn.execute("SELECT * FROM households WHERE id = ?", (hid,)).fetchone()
        own = token or store.current_household_token(deps.conn, deps.secret, hid)
        return {**_household_json(deps.conn, row), "token": own}

    def pool(origin: PublicOrigin) -> None:
        ok, reopens_at = deps.pool_open(origin)
        if not ok:
            raise _cap_error("global_cap", reopens_at, "The free credits for new homes are used up for now.")

    door_opens: dict[str, list[datetime]] = defaultdict(list)  # per client address, today's door homes (memory)

    def door_refusal(who: str) -> HTTPException | None:
        """Why a new home through the door would be turned away now (403 shut, 429 capped), or None."""
        if not door.door_open(deps.conn):
            return HTTPException(403, {"error": "door_closed", "detail": "New homes open with an invite for now."})
        now = deps.clock()
        midnight = _next_utc_midnight(now)
        day = midnight - timedelta(days=1)
        door_opens[who] = [t for t in door_opens[who] if t >= day]
        if len(door_opens[who]) >= deps.door_homes_per_address_day:
            return _cap_error("address_cap", midnight, "This connection has opened today's new homes.")
        today = deps.conn.execute("SELECT count(*) FROM households WHERE joined_via = 'door' AND created_at >= ?",
                                  (store.now_iso(day),)).fetchone()[0]
        if today >= deps.door_homes_per_day:
            return _cap_error("door_cap", midnight, "Today's new homes are taken.")
        ok, reopens_at = deps.pool_open("door")
        if not ok:
            return _cap_error("global_cap", reopens_at, "The free credits for new homes are used up for now.")
        return None

    @r.get("/households/door")
    def door_state(request: Request) -> dict[str, Any]:
        """Whether a new home may open through the door now, so the phone can offer the replay first. Reads only."""
        refused = door_refusal(request.client.host if request.client else "?")
        if refused is None:
            return {"state": "open", "reopens_at": None}
        if refused.status_code == 403:
            return {"state": "shut", "reopens_at": None}
        assert isinstance(refused.detail, dict)
        return {"state": "capped", "reopens_at": refused.detail["reopens_at"]}

    @r.post("/households", status_code=201)
    def open_household(body: HouseholdIn, who: Annotated[str, Depends(throttle)]) -> dict[str, Any]:
        if body.invite_code is None:
            if body.age_ok is not True:
                raise HTTPException(422, "age_ok: required without an invite code")
            refused = door_refusal(who)
            if refused is not None:
                raise refused
            hid = door.open_door_household(deps.conn, country=body.country, lang=body.lang, at=deps.clock())
            door_opens[who].append(deps.clock())
            return created(hid)
        hid = store.claim_invite(deps.conn, body.invite_code, consent=body.consent, country=body.country,
                                 lang=body.lang, at=deps.clock())
        if hid is None:
            failures[who].append(time.monotonic())
            raise HTTPException(403, "bad or used invite code")
        return created(hid)

    @r.post("/households/try", status_code=201)
    def try_household(body: TryIn, who: Annotated[str, Depends(throttle)]) -> dict[str, Any]:
        """A judges' link: each use opens its own home under the link's caps, which live in its row."""
        now = deps.clock()
        link = door.live_link(deps.conn, body.code, at=now)
        if link is None:
            failures[who].append(time.monotonic())
            raise HTTPException(404, "no such link")
        if len(link.homes) >= link.max_homes:
            raise _cap_error("judges_full", None, "This link has opened all the homes it may.")
        if deps.spend is not None and deps.spend(link.homes) >= link.cap_usd_total:
            raise _cap_error("judges_cap", None, "This link's credits are used up.")  # its total, ever
        pool("judges")
        hid = door.open_link_household(deps.conn, link, country=body.country, lang=body.lang, at=now)
        if hid is None:  # another scan took the last place
            raise _cap_error("judges_full", None, "This link has opened all the homes it may.")
        return created(hid)

    @r.post("/households/return", status_code=201)
    def return_household(body: ReturnIn, who: Annotated[str, Depends(throttle)]) -> dict[str, Any]:
        """A return link, on another device: a new app token for the person who made it."""
        got = door.use_return_code(deps.conn, body.code, at=deps.clock())
        if got is None:
            failures[who].append(time.monotonic())
            raise HTTPException(404, "no such return link")
        hid, person_id = got
        return created(hid, door.issue_app_token(deps.conn, hid, person_id, at=deps.clock()))

    @r.post("/household/return-link", status_code=201)
    def return_link(c: Caller, request: Request) -> dict[str, str]:
        if c.by_kind != "app" or c.person_id is None:
            raise HTTPException(403, "only the household's own token or an app token makes a return link")
        p = deps.conn.execute("SELECT view_only FROM people WHERE id = ?", (c.person_id,)).fetchone()
        if p is None or p["view_only"]:
            raise HTTPException(403, "a view-only member cannot make a return link")
        code, expires_at = door.new_return_code(deps.conn, c.household_id, c.person_id, at=deps.clock())
        return {"url": f"{str(request.base_url).rstrip('/')}/#return/{code}",
                "expires_at": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z")}

    @r.get("/household")
    def get_household(hh: Household) -> dict[str, Any]:
        return _household_json(deps.conn, hh)

    @r.patch("/household")
    def patch_household(body: HouseholdPatchIn, c: AppOnly) -> dict[str, Any]:
        """The home's settings, by an admin: whether it rents (a landlord's job then says to ask the landlord)."""
        if "renter" not in body.model_fields_set:
            raise HTTPException(422, "nothing to change")
        renter = None if body.renter is None else int(body.renter)
        with deps.conn:
            deps.conn.execute("UPDATE households SET renter = ? WHERE id = ?", (renter, c.household_id))
        return _household_json(deps.conn, deps.conn.execute("SELECT * FROM households WHERE id = ?",
                                                 (c.household_id,)).fetchone())

    @r.delete("/household", status_code=204)
    def delete_household(c: AppOnly) -> Response:
        erase.household(deps.conn, c.household_id)  # its files, its ledger ids, then its rows
        return Response(status_code=204)

    @r.get("/household/people")
    def get_people(c: Caller) -> list[dict[str, Any]]:
        return [people.person_json(p, c.person_id) for p in people.people(deps.conn, c.household_id)]

    @r.patch("/household/people/{person_id}")
    def patch_person(person_id: str, body: PersonPatchIn, who: Me) -> dict[str, Any]:
        c, actor = who
        if not body.model_fields_set:
            raise HTTPException(422, "nothing to change")
        try:
            row = people.change_person(deps.conn, c.household_id, actor, person_id, name=body.name, role=body.role,
                                       view_only=body.view_only)
        except people.PeopleError as e:
            raise _people_error(e) from None
        return people.person_json(row, c.person_id)

    @r.delete("/household/people/{person_id}", status_code=204)
    def delete_person(person_id: str, who: Me) -> Response:
        c, actor = who
        try:
            people.remove_person(deps.conn, c.household_id, actor, person_id, at=deps.clock())
        except people.PeopleError as e:
            raise _people_error(e) from None
        return Response(status_code=204)

    @r.get("/me/alerts")
    def get_alerts(who: Me) -> dict[str, Any]:
        return people.alerts(deps.conn, who[1]["id"])

    @r.put("/me/alerts")
    def put_alerts(body: AlertsIn, who: Me) -> dict[str, Any]:
        """The caller's own alerts, from any role; agent, ha and feed tokens get 403."""
        c, p = who
        try:
            people.set_alerts(deps.conn, c.household_id, p["id"], push=body.push, calendar=body.calendar,
                              hub=body.hub, items=body.items, follows=body.follows,
                              quiet=(body.quiet.from_, body.quiet.to) if body.quiet else None)
        except people.PeopleError as e:
            raise _people_error(e) from None
        return people.alerts(deps.conn, p["id"])

    @r.post("/household/invites", status_code=201)
    def post_invite(body: InviteIn, c: AppOnly, request: Request) -> dict[str, Any]:
        try:
            inv = people.new_invite(deps.conn, c.household_id, str(c.person_id), role=body.role,
                                    view_only=body.view_only, email=body.email,
                                    expires_in_days=body.expires_in_days, at=deps.clock())
        except people.PeopleError as e:
            raise _people_error(e) from None
        return {"code": inv.code, "join_url": f"{str(request.base_url).rstrip('/')}/#join/{inv.code}",
                "role": inv.role, "view_only": inv.view_only, "email": inv.email,
                "expires_at": inv.expires_at.isoformat(timespec="seconds").replace("+00:00", "Z")}

    @r.post("/household/join", status_code=201)
    def post_join(body: JoinIn, who: Annotated[str, Depends(throttle)]) -> dict[str, Any]:
        try:
            hid, pid = people.join(deps.conn, body.code, body.name, at=deps.clock())
        except people.PeopleError as e:
            if e.error == "not_found":
                failures[who].append(time.monotonic())
                raise HTTPException(403, {"error": "forbidden", "detail": e.detail}) from None
            raise _people_error(e) from None
        p = people.person(deps.conn, hid, pid)
        assert p is not None
        hh = deps.conn.execute("SELECT * FROM households WHERE id = ?", (hid,)).fetchone()
        return {"token": door.issue_app_token(deps.conn, hid, pid, at=deps.clock()),
                "person": people.person_json(p, pid), "household": _household_json(deps.conn, hh)}

    @r.get("/household/feeds")
    def feeds(c: Caller, request: Request) -> dict[str, str]:
        """A person's own feed (what their alerts follow); an agent's or Home Assistant's, the whole home's."""
        p = people.person(deps.conn, c.household_id, c.person_id) if c.by_kind == "app" and c.person_id else None
        token = (store.person_feed_token(deps.secret, c.household_id, p["id"]) if p is not None
                 else store.feed_token(deps.secret, c.household_id))
        return {"ics_url": str(request.url_for("ics_feed", feed_token=token))}

    def _due(hh: sqlite3.Row) -> list[Due]:
        return due(deps.conn, hh["id"], deps.clock(), deps.load_card)

    @r.get("/due")
    def get_due(hh: Household, within_days: int | None = None) -> list[dict[str, object]]:
        items = _due(hh)
        if within_days is not None:
            if within_days < 0:
                raise HTTPException(422, "within_days >= 0")
            until = local_today(deps.clock(), household_tz(hh["country"])) + timedelta(days=within_days)
            items = [d for d in items if d.due_on is not None and d.due_on <= until]
        return [d.api() for d in items]

    def _job(hh: sqlite3.Row, appliance_id: str, job_id: str) -> Due:
        mine = [d for d in _due(hh) if d.appliance_id == appliance_id and d.job_id == job_id]
        if not mine:
            raise HTTPException(404, "no such job in this household's schedule")
        return mine[0]

    def _own_only(hh: sqlite3.Row, appliance_id: str, job_id: str, *, fix: int = 404) -> bool:
        """True for an upkeep job whose manual gives no interval: on /due only by a schedule a person set, so it is
        looked up on its card, on /due or not. 404 for no such job; a fault fix is `fix` (422 fix_has_no_schedule
        for PUT schedule)."""
        row = deps.conn.execute(
            "SELECT c.card_path FROM appliances a JOIN cards_ref c ON c.appliance_id = a.id"
            " WHERE a.household_id = ? AND a.id = ? AND a.deleted_at IS NULL", (hh["id"], appliance_id)).fetchone()
        card = deps.load_card(row["card_path"]) if row is not None and row["card_path"] else None
        job = next((j for j in card.jobs if j.id == job_id), None) if card is not None and checked(card) else None
        if job is None:
            raise HTTPException(404, "no such job in this household's schedule")
        if job.kind == "fix" and fix == 404:
            raise HTTPException(404, "no such job in this household's schedule")
        if job.kind == "fix":
            raise HTTPException(422, {"error": "fix_has_no_schedule", "detail": "a fault fix has no schedule"})
        return job.interval is None

    def _maybe_job(hh: sqlite3.Row, appliance_id: str, job_id: str) -> Due | None:
        mine = [d for d in _due(hh) if d.appliance_id == appliance_id and d.job_id == job_id]
        return mine[0] if mine else None

    def _today(hh: sqlite3.Row) -> date:
        return local_today(deps.clock(), household_tz(hh["country"]))

    def _write(c: store.Caller, appliance_id: str, job_id: str, change: Callable[[Due], None]) -> dict[str, object]:
        """Run one schedule change for a job in this household's schedule; the job's new Due."""
        d = _job(c.household, appliance_id, job_id)
        try:
            change(d)
        except store.NotFound:
            raise HTTPException(404, "no such appliance") from None
        except manage.NotOwn as e:
            raise HTTPException(409, {"error": "not_own", "detail": str(e)}) from None
        except manage.Conflict as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return _job(c.household, appliance_id, job_id).api()

    @r.put("/appliances/{appliance_id}/jobs/{job_id}/last-done")
    def put_last_done(appliance_id: str, job_id: str, body: LastDoneIn, c: Writer) -> dict[str, object]:
        return _write(c, appliance_id, job_id, lambda d: manage.set_last_done(
            deps.conn, c.household_id, appliance_id, job_id, source=body.source, on=body.on,
            today=_today(c.household), by=c, at=deps.clock()))

    @r.post("/appliances/{appliance_id}/jobs/{job_id}/done")
    def post_done(appliance_id: str, job_id: str, c: Writer, body: DoneIn | None = None) -> dict[str, object]:
        today = _today(c.household)
        return _write(c, appliance_id, job_id, lambda d: manage.mark_done(
            deps.conn, c.household_id, appliance_id, job_id, on=(body.on if body else None) or today, today=today,
            by=c, at=deps.clock()))

    @r.put("/appliances/{appliance_id}/jobs/{job_id}/assignee")
    def put_assignee(appliance_id: str, job_id: str, body: AssigneeIn, c: Writer) -> dict[str, object]:
        return _write(c, appliance_id, job_id, lambda d: manage.set_assignee(
            deps.conn, c.household_id, appliance_id, job_id, person_id=body.person_id, by=c, at=deps.clock()))

    @r.put("/appliances/{appliance_id}/jobs/{job_id}/snooze")
    def put_job_snooze(appliance_id: str, job_id: str, body: JobSnoozeIn, c: Writer) -> dict[str, object]:
        return _write(c, appliance_id, job_id, lambda d: manage.snooze(
            deps.conn, c.household_id, appliance_id, job_id, until=body.until,
            due_on=d.due_on, today=_today(c.household), by=c, at=deps.clock()))

    def _write_off_due(c: store.Caller, appliance_id: str, job_id: str, change: Callable[[], None]) -> Any:
        """A change to a job whose manual gives no interval, on /due or not: its new Due, or 204 once it left /due."""
        try:
            change()
        except store.NotFound:
            raise HTTPException(404, "no such appliance") from None
        except manage.NotOwn as e:
            raise HTTPException(409, {"error": "not_own", "detail": str(e)}) from None
        except manage.Conflict as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        d = _maybe_job(c.household, appliance_id, job_id)
        return d.api() if d is not None else Response(status_code=204)

    @r.put("/appliances/{appliance_id}/jobs/{job_id}/schedule", response_model=None)
    def put_schedule(appliance_id: str, job_id: str, body: ScheduleIn, c: Writer) -> Any:
        """Any upkeep job. For one whose manual gives no interval only a person in the app sets it: no model or
        automation proposes an interval the manual does not give."""
        if not _own_only(c.household, appliance_id, job_id, fix=422):
            return _write(c, appliance_id, job_id, lambda d: manage.set_schedule(
                deps.conn, c.household_id, appliance_id, job_id, schedule=body.schedule(), by=c, at=deps.clock()))
        if c.by_kind != "app":
            raise HTTPException(403, {"error": "person_only", "detail": "the manual gives no interval for this job, "
                                      "so only a person in the app can set one"})
        return _write_off_due(c, appliance_id, job_id, lambda: manage.set_schedule(
            deps.conn, c.household_id, appliance_id, job_id, schedule=body.schedule(), by=c, at=deps.clock()))

    @r.post("/appliances/{appliance_id}/jobs/{job_id}/undo", response_model=None)
    def post_undo(appliance_id: str, job_id: str, c: Writer, body: UndoIn | None = None) -> Any:
        own = body is not None and body.own
        if not _own_only(c.household, appliance_id, job_id):
            return _write(c, appliance_id, job_id, lambda d: manage.undo(
                deps.conn, c.household_id, appliance_id, job_id, by=c, at=deps.clock(), own=own))
        return _write_off_due(c, appliance_id, job_id, lambda: manage.undo(
            deps.conn, c.household_id, appliance_id, job_id, by=c, at=deps.clock(), own=own))

    @r.get("/appliances/{appliance_id}/jobs/{job_id}/changes")
    def get_changes(appliance_id: str, job_id: str, c: Caller) -> list[dict[str, Any]]:
        if not _own_only(c.household, appliance_id, job_id):
            _job(c.household, appliance_id, job_id)
        return manage.changes(deps.conn, c.household_id, appliance_id, job_id)

    @r.post("/household/tokens", status_code=201)
    def new_token(body: TokenIn, c: AppOnly) -> dict[str, Any]:
        try:
            tid, token = store.issue_token(deps.conn, c.household_id, by_kind=body.by_kind, scope=body.scope,
                                           label=body.label, created_by=c.person_id, at=deps.clock())
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"id": tid, "token": token}

    def _private() -> Path:
        if deps.private is None:
            raise HTTPException(503, "this server takes no feedback")
        return deps.private

    @r.post("/household/feedback", status_code=201)
    def post_feedback(body: FeedbackIn, me: Me) -> dict[str, str]:
        """A pilot household's answers to the feedback form, kept as typed; the id is the one to withdraw it by.
        Any person in the home sends it from the app; an agent or Home Assistant cannot."""
        c, _ = me
        if deps.conn.execute("SELECT 1 FROM pilot_cohort WHERE id = ?", (c.household_id,)).fetchone() is None:
            raise HTTPException(403, {"error": "not_pilot", "detail": "only a pilot household sends feedback"})
        try:
            rec = feedback.record(c.household_id, source="app", lang=body.lang,
                                  answers=body.answers.model_dump(mode="json"), quote=body.quote,
                                  quote_name=body.quote_name, at=deps.clock())
        except ValidationError as e:  # what the form's own model cannot say: a name exactly when quoted by name
            raise HTTPException(422, e.message) from None
        try:
            written = feedback.append(_private(), rec, deleting=erase.deleting)
        except feedback.FeedbackError as e:
            raise HTTPException(429, {"error": e.error, "detail": e.detail}) from None
        if not written:
            raise HTTPException(410, {"error": "household_deleted", "detail": "this home is being deleted"})
        return {"id": rec["id"]}

    @r.post("/household/feedback/{feedback_id}/withdraw", status_code=204)
    def withdraw_feedback(feedback_id: str, me: Me) -> Response:
        """Never print this answer's words again: its quote is withdrawn and the name blanked. Withdrawing twice
        is fine; another home's answer is not found."""
        c, _ = me
        try:
            feedback.withdraw(_private(), feedback_id, household_id=c.household_id, at=deps.clock())
        except feedback.FeedbackError as e:
            raise HTTPException(404, {"error": e.error, "detail": e.detail}) from None
        return Response(status_code=204)

    @r.get("/household/tokens")
    def get_tokens(c: AppOnly) -> list[dict[str, Any]]:
        return [dict(t) for t in store.list_tokens(deps.conn, c.household_id)]

    @r.delete("/household/tokens/{token_id}", status_code=204)
    def delete_token(token_id: str, c: AppOnly) -> Response:
        if not store.revoke_token(deps.conn, c.household_id, token_id, at=deps.clock()):
            raise HTTPException(404, "no such token")
        return Response(status_code=204)

    @r.get("/feeds/{feed_token}.ics", name="ics_feed")
    def ics_feed(feed_token: str, request: Request) -> Response:
        found = store.from_feed_token(deps.conn, deps.secret, feed_token)
        if found is None:
            raise HTTPException(404, "not found")
        row, person_id = found
        base = str(request.base_url).split("/api/")[0]
        body = ics.feed(deps.conn, row["id"], deps.clock(), deps.load_card, base, person_id=person_id)
        return Response(body, media_type="text/calendar; charset=utf-8",
                        headers={"Cache-Control": "private, max-age=900"})

    @r.get("/push/vapid-public-key")
    def vapid_key(hh: Household) -> dict[str, str]:
        if deps.vapid is None:
            raise HTTPException(503, "push is not configured on this server")
        return {"key": deps.vapid.public_key}

    @r.post("/push/subscriptions", status_code=201)
    def add_subscription(body: PushSubscriptionIn, who: Me) -> Response:
        c, p = who  # anyone in the home, a viewer too: their own browser, for their own alerts
        try:
            push.subscribe(deps.conn, c.household_id, body.model_dump(), person_id=p["id"], at=deps.clock())
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return Response(status_code=201)

    @r.delete("/push/subscriptions", status_code=204)
    def remove_subscription(body: EndpointIn, who: Me) -> Response:
        push.unsubscribe(deps.conn, who[0].household_id, body.endpoint)
        return Response(status_code=204)

    @r.post("/reminders/{reminder_key}/opened", status_code=204)
    def opened(reminder_key: str, body: OpenedIn, c: Writer) -> Response:
        if not store.record_reminder_opened(deps.conn, c.household_id, reminder_key, body.channel, at=deps.clock()):
            raise HTTPException(404, "no such reminder")
        return Response(status_code=204)

    @r.post("/reminders/{reminder_key}/snooze", status_code=204)
    def snooze(reminder_key: str, c: Writer, body: SnoozeIn | None = None) -> Response:
        hours = body.hours if body else 24
        if not store.record_reminder_snoozed(deps.conn, c.household_id, reminder_key, hours, at=deps.clock()):
            raise HTTPException(404, "no such reminder")
        return Response(status_code=204)

    @r.get("/preflight")
    async def run_preflight(request: Request) -> list[dict[str, object]]:
        if deps.preflight is None:
            return [Line("preflight", False, "not wired").json()]
        url = request.url
        hosts = [(url.hostname, url.port or 443)] if url.scheme == "https" and url.hostname else []
        checks = deps.preflight(hosts)
        if not hosts:
            checks.append(("certificate", no_cert))
        return [line.json() for line in await run_checks(checks)]

    @r.post("/preflight/warm-up", response_model=None)
    async def warm_up() -> dict[str, object] | JSONResponse:
        """The page's button (contracts 1.5.0). 409 while one runs, and for 60 s after one finished."""
        if deps.warm_up is None:
            return Line("warm up", False, "no warm-up in this build").json()
        if warming.locked():
            return JSONResponse({"error": "warming_up", "detail": "a warm-up is already running"}, 409)
        now = deps.clock()
        if warm_again_at and now < warm_again_at[0]:
            wait = int((warm_again_at[0] - now).total_seconds()) + 1
            return JSONResponse({"error": "cooldown", "detail": f"warmed up just now; try again in {wait} s"}, 409,
                                headers={"Retry-After": str(wait)})
        async with warming:
            r = await deps.warm_up()
        warm_again_at[:] = [deps.clock() + deps.warm_up_cooldown]
        return Line("warm up", bool(r.get("ok")), str(r.get("detail", ""))).json()

    return r
