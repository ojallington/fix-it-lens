"""The integration's core, with no Home Assistant import (so it is tested in the Fix-It Lens repo).

It reads the household's schedule from the Fix-It Lens REST API (GET /api/v1/due, /appliances, /household) with a
token the household made for its Home Assistant, and writes back only what a person asked for: a job done (ticked in
the to-do list, the fixit_lens.mark_done service, a notification's Done button) or snoozed to a later date. Each one
shows in the job's history as Home Assistant's, and can be undone in the app. It never computes a date itself; the
server works out every date from the manual and the household's own schedule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

API = "/api/v1"
TIMEOUT_S = 10.0


class AuthFailed(Exception):
    """The household token was refused (401/403): the config entry needs a new one."""


class Refused(Exception):
    """The server said no to a change, in its own words (a snooze that is not later, a date in the future)."""


class ReadOnly(Refused):
    """The token can only read: ticking off, snoozing and adding need one that can write."""


class Unreachable(Exception):
    """The server did not answer, or answered with something other than the contract."""


@dataclass(frozen=True)
class Job:
    appliance_id: str
    alias: str
    job_id: str
    title: str
    due_on: date | None
    last_done: date | None
    last_done_source: str
    interval_quote: str | None           # None: the manual gives no interval; the schedule is the household's
    page: int | None
    duration_min: int | None
    status: str = "none"                 # upcoming | due | overdue | none (no date yet)
    window_end: date | None = None       # the household's window: overdue after this day
    snoozed_until: date | None = None
    schedule: str | None = None          # the household's own, in words: "every 3 to 6 months"; None: the manual's
    link: str | None = None              # the job in the app, where a repair starts
    due_name: str | None = None          # the server's short noun for the job ("debris filter"), when it sends one

    @property
    def key(self) -> str:
        return f"{self.appliance_id}:{self.job_id}"

    @property
    def name(self) -> str:
        """The job in a few words, as a reminder names it: "Debris filter" for "Clean the debris filter"."""
        return cap(self.due_name or short_name(self.title))

    @property
    def summary(self) -> str:
        """A to-do item or calendar event: "Dishwasher: Clean the debris filter"."""
        return f"{cap(self.alias)}: {self.title}"

    @property
    def short_summary(self) -> str:
        """A calendar event, where a month view has room for a few words: "Dishwasher: Debris filter"."""
        return f"{cap(self.alias)}: {self.name}"

    @property
    def object_id(self) -> str:
        """sensor.<item>_next_due, with <item> = alias and job: sensor.dishwasher_clean_filters_next_due."""
        return f"{slug(self.alias)}_{slug(self.job_id)}_next_due"

    def description(self) -> str:
        last = (f"last done {self.last_done.isoformat()}" if self.last_done
                else "last done: never" if self.last_done_source == "never" else "last done: not known")
        mins = f"{self.duration_min} minutes · " if self.duration_min else ""
        snoozed = f" · snoozed to {self.snoozed_until.isoformat()}" if self.snoozed_until else ""
        if self.interval_quote is None:
            return f"{mins}you chose {self.schedule} · the manual gives no interval · {last}{snoozed}"
        yours = f" · you chose {self.schedule}" if self.schedule else ""
        return f"{mins}{self.interval_quote}{yours} · {last}{snoozed}"

    def attributes(self) -> dict[str, Any]:
        return {"appliance": self.alias, "job_id": self.job_id, "title": self.title, "interval": self.interval_quote,
                "page": self.page,
                "last_done": self.last_done.isoformat() if self.last_done else None,
                "last_done_source": self.last_done_source, "duration_min": self.duration_min,
                "status": self.status, "window_end": self.window_end.isoformat() if self.window_end else None,
                "snoozed_until": self.snoozed_until.isoformat() if self.snoozed_until else None,
                "your_schedule": self.schedule, "job": self.key, "link": self.link}

    def event(self) -> dict[str, Any]:
        """The fixit_lens_job_due event's data: enough for a notification with a Done button and a link."""
        return {"job": self.key, "appliance": self.alias, "title": self.title, "status": self.status,
                "due_on": self.due_on.isoformat() if self.due_on else None,
                "window_end": self.window_end.isoformat() if self.window_end else None,
                "interval": self.interval_quote, "page": self.page, "link": self.link}


@dataclass(frozen=True)
class Appliance:
    """One of the household's appliances, as GET /appliances gives it: a device in Home Assistant."""

    id: str
    alias: str
    kind: str | None = None
    brand: str | None = None
    model: str | None = None

    @property
    def name(self) -> str:
        return cap(self.alias)

    @property
    def maker(self) -> str | None:
        """The card's maker slug as a name: "bosch" -> "Bosch", "aeg" -> "AEG", "de-dietrich" -> "De Dietrich"."""
        if not self.brand:
            return None
        if self.brand != self.brand.lower():
            return self.brand  # already a name
        return " ".join(w.upper() if w in ACRONYMS else cap(w) for w in re.split(r"[-_\s]+", self.brand) if w)


@dataclass(frozen=True)
class Snapshot:
    """Everything one poll reads: the home, its appliances by id, and its jobs (soonest first)."""

    home: str | None
    appliances: dict[str, Appliance]
    jobs: list[Job]

    def appliance(self, appliance_id: str) -> Appliance:
        """The appliance a job belongs to; one /appliances does not list stands in under the job's own alias."""
        a = self.appliances.get(appliance_id)
        if a is not None:
            return a
        alias = next((j.alias for j in self.jobs if j.appliance_id == appliance_id), appliance_id)
        return Appliance(appliance_id, alias)

    def appliance_ids(self) -> list[str]:
        return list(dict.fromkeys([*self.appliances, *(j.appliance_id for j in self.jobs)]))


# Makers whose name is written in capitals
ACRONYMS = frozenset({"aeg", "atag", "ge", "hp", "lg"})


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def short_name(title: str) -> str:
    """The object of a job's title, the way the app's reminders name it (app/schedule/due.py due_name):
    "Clean the debris filter" -> "debris filter", "Pompfilter schoonmaken" -> "pompfilter"."""
    title = title.strip().rstrip(".")
    words = title.split()
    low = f" {title.lower()} "
    if " the " in low:                                    # en: the object after the article
        name = title[low.index(" the ") + 4:]
    elif len(words) > 1 and words[-1].lower().endswith("en"):  # nl: the object before the infinitive
        name = " ".join(words[:-1])
    elif len(words) > 1:                                  # en without an article: drop the verb
        name = " ".join(words[1:])
    else:
        name = title
    return name[:1].lower() + name[1:]


def schedule_words(s: Any) -> str | None:
    """The household's own schedule (contracts' Schedule) in words, or None for the manual's."""
    if not isinstance(s, dict):
        return None
    unit = str(s.get("unit", ""))
    if s.get("kind") == "every" and s.get("every"):
        n = int(s["every"])
        return f"every {unit}" if n == 1 else f"every {n} {unit}s"
    if s.get("kind") == "window" and s.get("min") and s.get("max"):
        return f"every {s['min']} to {s['max']} {unit}s"
    return None


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "item"


def _date(v: Any) -> date | None:
    return date.fromisoformat(v) if isinstance(v, str) and v else None


def parse_due(rows: list[dict[str, Any]], aliases: dict[str, str], origin: str | None = None) -> list[Job]:
    jobs = []
    for r in rows:
        last = r.get("last_done") or {}
        aid, jid = str(r["appliance_id"]), str(r["job_id"])
        jobs.append(Job(
            appliance_id=str(r["appliance_id"]), alias=aliases.get(str(r["appliance_id"]), str(r["appliance_id"])),
            job_id=str(r["job_id"]), title=str(r["title"]), due_on=_date(r.get("due_on")),
            last_done=_date(last.get("date")), last_done_source=str(last.get("source") or "unknown"),
            interval_quote=r.get("interval_quote") or None, page=r.get("page"),
            duration_min=r.get("duration_min"), status=str(r.get("status") or "none"),
            window_end=_date(r.get("window_end")), snoozed_until=_date(r.get("snoozed_until")),
            schedule=schedule_words(r.get("schedule")), link=f"{origin}/#job/{aid}/{jid}" if origin else None,
            due_name=r.get("due_name") or None,
        ))
    return sorted(jobs, key=lambda j: (j.due_on is None, j.due_on or date.max, j.key))


class Client:
    """In HA, pass HA's shared client (`get_async_client(hass)`); tests pass a transport."""

    def __init__(self, base_url: str, token: str, *, http: httpx.AsyncClient | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._own = http is None
        self._http = http or httpx.AsyncClient(transport=transport)

    async def close(self) -> None:
        if self._own:
            await self._http.aclose()

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = await self._http.request(method, self.base_url + API + path, headers=self._headers,
                                         timeout=TIMEOUT_S, **kw)
        except httpx.HTTPError as e:
            raise Unreachable(f"{self.base_url}: {type(e).__name__}") from e
        if r.status_code == 403 and method != "GET":
            raise ReadOnly("This Home Assistant token can only read. Make one that can write, and add it here.")
        if r.status_code in (401, 403):
            raise AuthFailed(r.json().get("detail", "token refused") if _is_json(r) else "token refused")
        return r

    async def household(self) -> dict[str, Any]:
        """The household (checks the token); config flow calls this before it saves an entry."""
        r = await self._call("GET", "/household")
        if r.status_code != 200 or not _is_json(r):
            raise Unreachable(f"GET /household answered {r.status_code}")
        data: dict[str, Any] = r.json()
        return data

    async def appliances(self) -> list[Appliance]:
        """The household's appliances from GET /appliances; [] when the server has no such route."""
        r = await self._call("GET", "/appliances")
        if r.status_code != 200 or not _is_json(r):
            return []
        return [Appliance(str(a["id"]), str(a["alias"]), _text(a.get("kind")), _text(a.get("brand")),
                          _text(a.get("model")))
                for a in r.json() if isinstance(a, dict) and "id" in a and "alias" in a]

    async def aliases(self) -> dict[str, str]:
        """appliance id -> alias."""
        return {a.id: a.alias for a in await self.appliances()}

    async def jobs(self, appliances: list[Appliance] | None = None) -> list[Job]:
        r = await self._call("GET", "/due")
        if r.status_code != 200 or not _is_json(r):
            raise Unreachable(f"GET /due answered {r.status_code}")
        apps = await self.appliances() if appliances is None else appliances
        return parse_due(r.json(), {a.id: a.alias for a in apps}, self.base_url)

    async def snapshot(self) -> Snapshot:
        home = await self.household()
        apps = await self.appliances()
        jobs = await self.jobs(apps)
        return Snapshot(home_name(home), {a.id: a for a in apps}, jobs)

    async def mark_done(self, job: Job, on: date) -> None:
        """Someone did the job on `on` (today or earlier); the server moves the next date from it."""
        await self._write("POST", job, "done", {"date": on.isoformat()})

    async def snooze(self, job: Job, until: date) -> None:
        """Move the job's due date later, to `until`; the server refuses a date that is not later."""
        await self._write("PUT", job, "snooze", {"until": until.isoformat()})

    async def add(self, found: Found) -> None:
        """Add one appliance the user ticked in the discovery list: its maker, model, kind and name, nothing else. A
        model the card library has links its card; any other gets its manual's pages, and no card is built."""
        r = await self._call("POST", "/appliances", json=found.body())
        if r.status_code in (409, 422) and _is_json(r):
            raise Refused(str(r.json().get("detail", "refused")))
        if r.status_code not in (200, 201):
            raise Unreachable(f"POST /appliances answered {r.status_code}")

    async def _write(self, method: str, job: Job, what: str, body: dict[str, Any]) -> None:
        r = await self._call(method, f"/appliances/{job.appliance_id}/jobs/{job.job_id}/{what}", json=body)
        if r.status_code == 422 and _is_json(r):
            raise Refused(str(r.json().get("detail", "refused")))
        if r.status_code not in (200, 204):
            raise Unreachable(f"{method} {what} answered {r.status_code}")


def home_name(household: dict[str, Any]) -> str | None:
    """The home's own name from GET /household, or None when it has none."""
    return _text(household.get("name"))


def _text(v: Any) -> str | None:
    """A name the server gave, or None for an empty one or the contract's "unknown"."""
    return str(v).strip() if isinstance(v, str) and v.strip() and v.strip().lower() != "unknown" else None


def _is_json(r: httpx.Response) -> bool:
    return r.headers.get("content-type", "").startswith("application/json")


# Discovery: the appliances Home Assistant already knows, offered as a list to tick. Only a ticked one's maker, model,
# kind and name leave Home Assistant (Found.body()).

# A kind from the device's name or model, in English, Dutch, German and French. The first that matches wins, so a
# "dishwasher" is not a washer and a washer-dryer is a washer.
KIND_WORDS: list[tuple[str, re.Pattern[str]]] = [(k, re.compile(p, re.I)) for k, p in [
    ("dishwasher", r"dish ?washer|vaatwas|geschirrsp(ü|ue)l|lave-vaisselle|afwasmachine"),
    ("washer", r"washer|washing machine|wasmachine|waschmaschine|waschtrockner|wasdroger|lave-linge"),
    ("dryer", r"dryer|tumble|droger|droogtrommel|trockner|s(è|e)che-linge"),
    ("oven", r"\boven|backofen|\bofen\b|\bfour\b|magnetron|microwave|mikrowelle|steamer|stoomoven|dampfgarer"),
    ("hob", r"\bhob\b|cooktop|kookplaat|kochfeld|induction|inductie|plaque"),
    ("hood", r"\bhood\b|afzuigkap|dunstabzug|abzugshaube|hotte"),
    ("fridge", r"fridge|refrigerator|freezer|koelkast|vriezer|k(ü|ue)hlschrank|gefrier|r(é|e)frig(é|e)rateur"),
    ("boiler", r"boiler|heat ?pump|warmtepomp|w(ä|ae)rmepumpe|cv-ketel|water heater|chauffe-eau"),
    ("other", r"coffee|koffie|kaffee|espresso|caf(é|e)"),
]]

# Bosch, Siemens, Neff and Gaggenau (BSH) codes start with the kind; Home Connect names a device by its type only
# sometimes, but its model is always the code.
BSH = frozenset({"bosch", "siemens", "neff", "gaggenau", "constructa", "bsh"})
BSH_PREFIX: list[tuple[str, str]] = [
    ("dishwasher", "SM SN SB SX SR SP SK SC S5 S9"), ("washer", "WA WG WM WN WU WI WD WV WW"),
    ("dryer", "WT WQ"), ("oven", "HB HM HN HR HS CM CB CS CD BF BE B1 B4 B5 C1"), ("hob", "PI PV PX PK PU EX EH T5"),
    ("hood", "DW DF DH DI LC LB LF D9"), ("fridge", "KI KG KA KS KF KU GI GS"),
    ("other", "TI TQ CT"),
]

# The integrations whose devices are appliances by nature: a device from them is offered when its kind is known.
# From any other a device needs its kind in its name or model ("Kitchen light" is not offered, "Vaatwasser" is).
APPLIANCE_SOURCES = frozenset({"home_connect", "miele", "smartthings", "matter", "electrolux", "whirlpool",
                               "lg_thinq", "aeg", "candy", "haier", "connectlife"})


@dataclass(frozen=True)
class Found:
    """A device Home Assistant knows that looks like an appliance, as the discovery list offers it."""

    device_id: str          # Home Assistant's device id: the list's key, never sent
    brand: str
    model: str
    kind: str
    alias: str

    @property
    def label(self) -> str:
        """The list's line: "Dishwasher: Bosch SMV4HAX40N"."""
        maker = Appliance("", "", brand=self.brand).maker or self.brand
        return f"{self.alias}: {maker} {self.model}"

    def body(self) -> dict[str, str]:
        """What POST /appliances gets: these four fields and nothing else."""
        return {"brand": self.brand, "model": self.model, "kind": self.kind, "alias": self.alias}


def base_model(model: str) -> str:
    """The model without a BSH production suffix: "SMV4HAX40N/39" -> "SMV4HAX40N" (the server matches the same way)."""
    return re.sub(r"\s*/\s*\d{1,3}\s*$", "", model.strip()).upper()


def maker_slug(manufacturer: str) -> str:
    """"BOSCH" -> "bosch", "De Dietrich" -> "de-dietrich", "BSH Hausgeräte GmbH" -> "bsh"."""
    words = re.sub(r"\b(gmbh|ag|inc|ltd|corp|co|bv|b\.v\.|hausger(ä|ae)te|home appliances|electronics)\b\.?", "",
                   manufacturer.strip().lower())
    return "-".join(w for w in re.split(r"[\s_,]+", words) if w)


def kind_of(text: str, maker: str, model: str) -> str | None:
    for kind, pattern in KIND_WORDS:
        if pattern.search(text):
            return kind
    if maker in BSH:
        code = model.upper()
        for kind, prefixes in BSH_PREFIX:
            if any(code.startswith(p) for p in prefixes.split()):
                return kind
    return None


def _model_code(*candidates: str | None) -> str | None:
    """The first that reads as a model code (letters and digits, a digit among them), else the first given."""
    given = [c.strip() for c in candidates if c and c.strip()]
    for c in given:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./ -]{2,58}", c) and re.search(r"\d", c):
            return c
    return given[0][:60] if given else None


def discovered(device_id: str, *, manufacturer: str | None, model: str | None, name: str | None,
               model_id: str | None = None, source: str | None = None, only_lights: bool = False) -> Found | None:
    """A device as the discovery list offers it, or None when it is not an appliance (no maker, no model, no kind)."""
    code = _model_code(model, model_id)
    maker = maker_slug(manufacturer or "")
    if not maker or not code or only_lights:
        return None
    label = (name or "").strip()
    kind = kind_of(f"{label} {model or ''} {model_id or ''}", maker, code)
    if kind is None or (source not in APPLIANCE_SOURCES and not any(p.search(f"{label} {model or ''}")
                                                                    for _, p in KIND_WORDS)):
        return None
    alias = label[:40].strip() or cap(kind if kind != "other" else "appliance")
    return Found(device_id, maker, code, kind, alias)


def offer(devices: list[Found], home: list[Appliance]) -> list[Found]:
    """The list to tick: the ones the home does not have yet (same maker and model), each with a name of its own."""
    have = {(a.brand or "").lower() + ":" + base_model(a.model or "") for a in home}
    taken = {a.alias.lower() for a in home}
    out: list[Found] = []
    for f in devices:
        key = f"{f.brand}:{base_model(f.model)}"
        if key in have:
            continue
        have.add(key)
        alias = f.alias
        if alias.lower() in taken:  # posting a name the home has would re-run that appliance's add
            alias = f"{alias} {base_model(f.model)}"[:40].strip()
        if alias.lower() in taken:
            continue
        taken.add(alias.lower())
        out.append(Found(f.device_id, f.brand, f.model, f.kind, alias))
    return out


# A ready dashboard from Home Assistant's own cards only (no custom card, no JavaScript): the to-do list and the
# calendar, then a section per appliance with its next job, when it is due, how many are overdue, a red or amber line
# when one is overdue or due, and a link to the job in the app. The markdown card is the one built-in card that can
# read the job's link from the sensor, so the button is a link in it.

# The markdown card's template, with `n` set to the appliance's next-job sensor: "**Filters**: due in 3 days", and the
# link to the job in the app (its address and ids, no token).
NEXT_JOB = "\n".join([
    "{%- set due = state_attr(n, 'due_on') -%}",
    "{%- if due -%}",
    "{%- set d = (strptime(due, '%Y-%m-%d').date() - now().date()).days -%}",
    "**{{ states(n) }}**: due {{ 'today' if d == 0 else 'tomorrow' if d == 1 else 'yesterday' if d == -1",
    "  else ('in %d days' % d) if d > 0 else ('%d days ago' % -d) }}",
    "{%- else -%}",
    "No job with a date yet.",
    "{%- endif -%}",
    "{%- if state_attr(n, 'link') %}",
    "",
    "[Open the job in Fix-It Lens]({{ state_attr(n, 'link') }})",
    "{%- endif -%}",
])


KIND_ICONS = {"dishwasher": "mdi:dishwasher", "washer": "mdi:washing-machine", "dryer": "mdi:tumble-dryer",
              "oven": "mdi:stove", "hob": "mdi:gas-burner", "hood": "mdi:fan", "fridge": "mdi:fridge",
              "boiler": "mdi:water-boiler", "other": "mdi:coffee-maker"}


def dashboard(aliases: list[str], title: str = "Upkeep") -> dict[str, Any]:
    """The dashboard for a home with these appliances (as Fix-It Lens names them), as HA's YAML mode reads it."""
    return {"title": title, "views": [{"title": title, "path": "upkeep", "icon": "mdi:wrench-clock",
                                       "type": "sections", "max_columns": 3,
                                       "sections": [_home_section(), *(_appliance_section(a) for a in aliases)]}]}


def _home_section() -> dict[str, Any]:
    return {"type": "grid", "cards": [
        {"type": "heading", "heading": "To do", "icon": "mdi:clipboard-check-outline"},
        {"type": "todo-list", "entity": "todo.fix_it_lens_upkeep", "hide_create": True},
        {"type": "calendar", "entities": ["calendar.fix_it_lens_upkeep"], "initial_view": "listWeek",
         "grid_options": {"columns": "full"}},
    ]}


def _appliance_section(alias: str) -> dict[str, Any]:
    a = slug(alias)
    n, overdue, due = f"sensor.{a}_next_job", f"sensor.{a}_overdue_jobs", f"sensor.{a}_due_jobs"
    return {"type": "grid", "cards": [
        {"type": "heading", "heading": cap(alias), "icon": KIND_ICONS.get(kind_of(alias, "", "") or "", "mdi:tools")},
        {"type": "conditional", "conditions": [{"condition": "numeric_state", "entity": overdue, "above": 0}],
         "card": {"type": "markdown", "text_only": True, "content":
                  f'<ha-alert alert-type="error">{{{{ states("{overdue}") }}}} overdue: '
                  f'{{{{ state_attr("{overdue}", "jobs") | join(", ") }}}}</ha-alert>'}},
        {"type": "conditional", "conditions": [{"condition": "numeric_state", "entity": due, "above": 0},
                                               {"condition": "numeric_state", "entity": overdue, "below": 1}],
         "card": {"type": "markdown", "text_only": True, "content":
                  f'<ha-alert alert-type="warning">{{{{ states("{due}") }}}} due now: '
                  f'{{{{ state_attr("{due}", "jobs") | join(", ") }}}}</ha-alert>'}},
        {"type": "entities", "state_color": True, "entities": [
            {"entity": n, "name": "Next job"},
            {"type": "attribute", "entity": n, "attribute": "due_on", "name": "Due on"},
            {"entity": due, "name": "Due now"},
            {"entity": overdue, "name": "Overdue"},
        ]},
        {"type": "markdown", "content": f"{{%- set n = '{n}' -%}}\n" + NEXT_JOB},
    ]}
