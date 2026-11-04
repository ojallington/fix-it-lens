"""The FastAPI server: the WebSocket at /ws and the REST routes app/ owns under /api/v1 (the schedule router is mounted
beside them when it is present).

WS: the first message is `hello`. With a known session_id and last_seq it resumes: hello_ok{resumed: true} and every
s2c message after last_seq from the 10-minute ring. With a replay_id it plays that recording: a `replay` badge
first, then the recorded s2c messages at their original offsets; nothing calls a model. Otherwise it opens a session:
`/ws?mode=capture` records frames with no model calls (for eval filming); `/ws?card=<brand>/<model>&job=<job_id>`
opens a repair on a checked card.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError

import calls
import rules
from app import cards, config, erase, recorder
from app.ports import HomeAssistant, Manuals
from app.session import Hooks, NoHooks, Session
from app.store import Store, StoreHooks
from contracts import unions
from contracts.models import ws


def cited_pages(card: Any) -> set[int]:
    steps = [st for j in card.jobs for st in j.steps if st.quote is not None]  # our own step cites no page
    pages = {st.quote.page for st in steps} | {st.page for st in steps if st.page is not None}
    return pages | {z.quote.page for z in card.red_zones} | {q.page for q in card.quotes}


def render_page(brand: str, model: str, page: int, scale: float = 2.0, household: str | None = None) -> bytes:
    import io

    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    from ingest.pages import manual_path

    path = manual_path(brand, model, household)
    if household is not None and not path.exists():  # the home's own copy first, then the shared one
        path = manual_path(brand, model)
    doc = pdfium.PdfDocument(str(path))
    try:
        if not 1 <= page <= len(doc):
            raise IndexError(page)
        im = doc[page - 1].render(scale=scale).to_pil()
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    finally:
        doc.close()


class Registry:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.sockets: dict[str, set[WebSocket]] = {}  # session id -> the sockets attached to it now
        self.loop: asyncio.AbstractEventLoop | None = None  # the loop the sessions run on
        self.store: Store | None = None  # where a session's end and outcome are written
        self._reapers: dict[str, asyncio.Task[None]] = {}  # session id -> its end once no phone has come back

    def get(self, session_id: str | None) -> Session | None:
        return self.sessions.get(session_id) if session_id else None

    async def end(self, s: Session, outcome: str) -> None:
        """A session over: forgotten (a resume finds nothing), its workers stopped, its sockets closed, and its
        outcome written to the record and the store. A second end is a no-op."""
        if self.sessions.get(s.session_id) is not s:
            return
        del self.sessions[s.session_id]
        reaper = self._reapers.pop(s.session_id, None)
        if reaper is not None and reaper is not asyncio.current_task():
            reaper.cancel()
        for sock in self.sockets.pop(s.session_id, set()):
            with contextlib.suppress(RuntimeError):  # already closed by the client
                await sock.close(code=1000)
        await s.close()
        s.rec.end(outcome, s.clock())
        if self.store is not None:
            self.store.session_ended(s, outcome, s.models.usd)

    def attached(self, s: Session) -> None:
        """A phone is on the session: a pending end for having been left is called off."""
        reaper = self._reapers.pop(s.session_id, None)
        if reaper is not None:
            reaper.cancel()

    def left(self, s: Session, after_s: float | None = None) -> None:
        """The last phone has gone: unless one comes back within `after_s`, the session ends as abandoned (or as
        done or stopped, if it got there), so nothing is left running and its end is on the record."""
        if self.sockets.get(s.session_id) or self.sessions.get(s.session_id) is not s:
            return

        async def reap() -> None:
            await asyncio.sleep(config.SESSION_LEFT_S if after_s is None else after_s)
            if not self.sockets.get(s.session_id):
                await self.end(s, outcome_of(s))

        self.attached(s)
        self._reapers[s.session_id] = asyncio.ensure_future(reap())

    async def end_household(self, household_id: str) -> dict[str, int]:
        """A deleted household's sessions: each socket closed with erase.CLOSE_CODE, each session stopped and
        forgotten (a resume finds nothing), so no frame or event is written after its files are swept."""
        gone = [s for s in self.sessions.values() if s.household == household_id]
        for s in gone:
            del self.sessions[s.session_id]
            for sock in self.sockets.pop(s.session_id, set()):
                with contextlib.suppress(RuntimeError):  # already closed by the client
                    await sock.close(code=erase.CLOSE_CODE, reason=erase.CLOSE_REASON)
            await s.close()
        return {"sessions_open": len(gone)}


def outcome_of(s: Session, by_person: bool = False) -> str:
    """How a session that is ending went: the store's outcome. Done or stopped if it got there; otherwise ended when
    the person tapped End, abandoned when no phone came back (the reaper)."""
    if s.phase == "job_done":
        return "done"
    if s.stopped:
        return "stopped"
    return "ended" if by_person else "abandoned"


def new_session_id() -> str:
    return secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")


def make_session(*, client: calls.Client, mode: str, record_root: Path | None, hello: ws.Hello,
                 card: Any = None, card_path: str | None = None, job_id: str | None = None,
                 manuals: Manuals | None = None, ha: HomeAssistant | None = None, hooks: Hooks | None = None,
                 witness_provider: Callable[..., Any] | None = None, guide: bool = True,
                 session_id: str | None = None, country: str = "nl", household: str | None = None,
                 cap_usd: float | None = None, appliance_id: str | None = None, alias: str | None = None,
                 fallback: calls.Client | None = None, lane: str = config.LANE) -> Session:
    if mode == "api" and getattr(getattr(client, "backend", None), "name", "") == "local":
        mode = "local"
    s = Session(session_id=session_id or new_session_id(), client=client, lang=hello.lang, country=country,
                mode=mode,  # type: ignore[arg-type]
                card=card, card_path=card_path, job_id=job_id, household=household, cap_usd=cap_usd,
                appliance_id=appliance_id, alias=alias, manuals=manuals, ha=ha, hooks=hooks or NoHooks(),
                record_root=record_root, fallback=fallback, lane=lane, preview=True, opening=True)
    if witness_provider is not None:
        s.witness_provider = witness_provider
    if guide and mode != "capture" and card is not None:
        from app.guide import Guide

        s.guide = Guide(s)
    return s


def create_app(*, country: str = "nl", client: calls.Client | None = None, manuals: Manuals | None = None,
               ha: HomeAssistant | None = None, hooks: Hooks | None = None, record_root: Path | None = None,
               replay_root: Path | None = None, witness_provider: Callable[..., Any] | None = None,
               store: Store | None = None, ha_for: Callable[[dict[str, list[str]]], HomeAssistant] | None = None,
               fallback: calls.Client | None = None, ingest: Callable[..., Any] | None = None) -> FastAPI:
    """`store` (the household store) is set in production: hello.household is then the bearer token, and a
    reminder's hello.appliance_id/job_id opens that appliance's card. `ha_for` builds the read-only HA client from an
    alias -> entities map."""
    app = FastAPI(title="Fix-It Lens")
    from app.feeds.errors import install_errors

    install_errors(app)  # every error is the contract's Error {error, detail}
    reg = Registry()
    reg.store = store
    app.state.registry = reg
    erase.watch(reg)
    root = record_root
    replays = replay_root or record_root
    # A replay folder is curated (deploy/curate.py copies into it); the sessions folder holds homes' records as made.
    curated = replay_root is not None

    def _manuals() -> Manuals:
        nonlocal manuals
        if manuals is None:
            from app.ports import IngestManuals

            manuals = IngestManuals()
        return manuals

    def _client() -> calls.Client:
        return client or calls.default_client()

    hooks_ = hooks or (StoreHooks(store) if store is not None else None)

    def _card(rel: str) -> cards.Loaded | None:
        """A card under cards/ (curated) or FIXIT_PRIVATE/cards (built when an appliance was added), checked by the
        loader; None when the path is anywhere else or missing."""
        path = (config.ROOT / rel).resolve()
        priv = config.private_dir()
        roots = [config.CARDS_DIR.resolve()] + ([(priv / "cards").resolve()] if priv is not None else [])
        if not any(r in path.parents for r in roots) or not path.exists():
            return None
        return cards.load(path, country, _manuals())

    async def _refuse(sock: WebSocket, kind: str) -> None:
        await sock.send_json({"type": "notice", "seq": 0, "t_ms": 0, "kind": kind, "text": None})
        await sock.close(code=1008)

    def _root() -> Path | None:
        return root if root is not None else config.sessions_dir()

    def _replay_id(raw: str) -> str:
        """A recording's id, with `golden` resolved to the curated one. FileNotFoundError when there is none."""
        rid = recorder.safe_id(raw)
        if rid == config.GOLDEN:
            rid = config.golden_replay(replays or config.sessions_dir()) or ""
            if not rid:
                raise FileNotFoundError(config.GOLDEN)
            rid = recorder.safe_id(rid)
            # Golden is shown to everyone: only a curated copy (deploy/curate.py: blurred, no household) may be it.
            if not curated or recorder.load(replays, rid).meta.household is not None:
                raise FileNotFoundError(config.GOLDEN)
        return rid

    def _load_for(rid: str, token: str | None) -> recorder.Recording:
        """A recording, for whoever may see it. Fails closed: a curated folder's recording (deploy/curate.py: blurred,
        no household) plays for anyone; a session as recorded holds the home's frames (its rooms, the rating plate
        and serial, whoever walked past) and plays only for its own household's token, and one that names no
        household plays for nobody. FileNotFoundError otherwise, so a refused id looks like no recording."""
        rec = recorder.load(replays or config.sessions_dir(), rid)
        owner = rec.meta.household
        if curated:
            if owner is not None:
                raise FileNotFoundError(rid)
        elif owner is None or _token_household(token) != owner:
            raise FileNotFoundError(rid)
        return rec

    def _bearer(authorization: str | None) -> str | None:
        scheme, _, token = (authorization or "").partition(" ")
        return token.strip() or None if scheme.lower() == "bearer" else None

    def _summary(meta: Any) -> dict[str, Any]:
        """What the replay's header and its job-done screen name, from the card: the appliance's kind (a recording is
        shown to strangers, so never the household's own name for it), the job's title, its number of steps."""
        out: dict[str, Any] = {"appliance_alias": None, "job_title": None, "steps_total": None}
        try:
            card = cards.read(config.ROOT / meta.card) if meta.card else None
        except (OSError, ValueError):
            card = None
        if card is None:
            return out
        out["appliance_alias"] = card.appliance_kind.replace("_", " ") if card.appliance_kind else None
        job = next((j for j in card.jobs if j.id == meta.job_id), None)
        if job is not None:
            titles = job.title.root
            out["job_title"] = titles.get(meta.lang) or titles.get("en") or next(iter(titles.values()))
            out["steps_total"] = len(job.steps)
        return out

    async def _play(sock: WebSocket, replay_id: str, speed: float, token: str | None) -> None:
        replay_id = _replay_id(replay_id)
        rec = _load_for(replay_id, token)
        await sock.send_json({"type": "replay", "seq": 0, "t_ms": 0, "badge": True, "replay_id": replay_id})
        last = 0.0
        for i, msg in enumerate(rec.s2c(), 1):
            if msg.get("type") == "hello_ok":
                continue
            wait = (float(msg.get("t_ms", 0)) - last) / 1000 / speed
            if wait > 0:
                await asyncio.sleep(min(wait, 5.0))
            last = float(msg.get("t_ms", 0))
            await sock.send_json({**msg, "seq": i})

    def _token_household(token: str | None) -> str | None:
        """The household a hello's token may act for: its own token, or a person's app token (not view-only)."""
        if not token or store is None:
            return None
        row = store.acting(token)
        return None if row is None else str(row["id"])

    def _deleted(token: str | None) -> bool:
        """A signed bearer token of a household that is being, or was, deleted in this process."""
        if not token or store is None:
            return False
        from app.schedule.store import current_household_token

        hid = token.partition("-")[0]
        return erase.deleting(hid) and hmac.compare_digest(token, current_household_token(store.conn, store.secret,
                                                                                          hid))

    @app.websocket("/ws")
    async def socket(sock: WebSocket) -> None:
        await sock.accept()
        q = sock.query_params
        try:
            hello = ws.Hello.model_validate(json.loads(await sock.receive_text()))
        except (ValidationError, ValueError, WebSocketDisconnect):
            await sock.close(code=1008)
            return
        if hello.replay_id:
            try:
                await _play(sock, recorder.safe_id(hello.replay_id), float(q.get("speed", "1")), hello.household)
            except (FileNotFoundError, ValueError):
                await sock.close(code=1008)
            return
        if _deleted(hello.household):  # nothing new starts while the household's files go, nor after
            await sock.close(code=erase.CLOSE_CODE, reason=erase.CLOSE_REASON)
            return
        token_household = _token_household(hello.household)
        if erase.deleting(token_household):  # a person's app token, while its household's files go
            await sock.close(code=erase.CLOSE_CODE, reason=erase.CLOSE_REASON)
            return
        if token_household is None and getattr(_client(), "mode", "replay") in ("record", "live"):
            await sock.close(code=1008)  # no spend without a household and its daily cap; resumes included
            return
        s = reg.get(hello.session_id)
        if s is not None and s.household is not None and s.household != token_household:
            await sock.close(code=1008)  # a household's session resumes only with that household's token
            return
        resumed = s is not None
        if s is None:
            mode = "capture" if q.get("mode") == "capture" else "api"
            hh = None
            if store is not None and hello.household:
                try:
                    hh = store.household(hello.household)
                except calls.BudgetExceeded:
                    await _refuse(sock, "cap_reached")
                    return
                if hh is None:
                    await sock.close(code=1008)
                    return
            if mode != "capture" and rules.load_country(hh.country if hh else country) is None:
                await _refuse(sock, "no_rules_for_country")  # no rules file: nothing could gate a sentence or a step
                return
            card_rel = f"cards/{q['card']}.yaml" if q.get("card") else None
            job_id = q.get("job") or hello.job_id
            appliance = alias = None
            house_ha = ha
            if hh is not None and hello.appliance_id:
                appliance = store.appliance(hh.id, hello.appliance_id) if store is not None else None
                if appliance is None:
                    await sock.close(code=1008)
                    return
                card_rel, alias = appliance.card_path or card_rel, appliance.alias
                if house_ha is None and ha_for is not None and store is not None and appliance.ha_entities:
                    house_ha = ha_for(store.entity_map(hh.id))
            card = None
            if mode != "capture" and card_rel:
                loaded = _card(card_rel)
                if loaded is None:
                    await sock.close(code=1008)
                    return
                if not loaded.usable:
                    await _refuse(sock, "ingest_failed")
                    return
                if job_id in loaded.refused:  # the rules refused this job; the card's other jobs still run
                    await _refuse(sock, "job_refused")
                    return
                card = loaded.card
            s = make_session(client=_client(), mode=mode, record_root=_root(), hello=hello, card=card,
                             card_path=card_rel if card is not None else None, job_id=job_id if card else None,
                             manuals=manuals, ha=house_ha, hooks=hooks_, witness_provider=witness_provider,
                             country=hh.country if hh else country, household=hh.id if hh else None,
                             cap_usd=hh.cap_usd if hh else None, appliance_id=appliance.id if appliance else None,
                             alias=alias, fallback=fallback, lane=hh.lane if hh else config.LANE)
            reg.sessions[s.session_id] = s
            reg.loop = asyncio.get_running_loop()
            if store is not None:
                store.session_started(s)
            s.start()
        await _attach(sock, s, hello, resumed)

    async def _attach(sock: WebSocket, s: Session, hello: ws.Hello, resumed: bool) -> None:
        async def send(msg: dict[str, Any]) -> None:
            await sock.send_json(msg)

        s.rec.client(s.clock(), "c2s", hello.model_dump(exclude_none=True))
        reg.sockets.setdefault(s.session_id, set()).add(sock)
        reg.attached(s)
        missed = [m for m in s.since(hello.last_seq) if m["type"] != "hello_ok"] if resumed else []
        s.sinks.append(send)
        await s.emit({"type": "hello_ok", "session_id": s.session_id, "resumed": resumed,
                      "mode": "local" if s.mode == "local" else "api"})
        if resumed:
            for m in missed:
                await sock.send_json(m)
            await s.notice("reconnected")
        if not resumed:
            async def begin() -> None:
                await s.greet()
                await s.start_job()

            asyncio.ensure_future(begin())
        try:
            while True:
                raw = json.loads(await sock.receive_text())
                try:
                    await s.handle(raw)
                except ValidationError:
                    continue  # a malformed message is dropped, never half-handled
                if raw.get("type") == "tap" and raw.get("action") == "end":  # the person ended it
                    await reg.end(s, outcome_of(s, by_person=True))
                    return
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            if send in s.sinks:
                s.sinks.remove(send)
            reg.sockets.get(s.session_id, set()).discard(sock)
            reg.left(s)

    api = APIRouter(prefix="/api/v1")

    @api.get("/replay/{session_id}")
    def replay(session_id: str, authorization: str | None = Header(None)) -> dict[str, Any]:
        try:
            rid = _replay_id(session_id)
            rec = _load_for(rid, _bearer(authorization))
        except (FileNotFoundError, ValueError):
            raise HTTPException(404) from None
        events = recorder.public_lines(rec)
        meta = json.loads(rec.meta.model_dump_json(exclude_none=True, exclude={"household"}))
        return {"meta": meta, "events": events,
                "replay_id": rid, "summary": _summary(rec.meta)}

    @api.get("/replay/{session_id}/frames/{frame_id}.jpg")
    def replay_frame(session_id: str, frame_id: str, authorization: str | None = Header(None)) -> FileResponse:
        try:
            d = _load_for(_replay_id(session_id), _bearer(authorization)).dir / "frames"
            p = d / f"{recorder.safe_id(frame_id)}.jpg"
        except (FileNotFoundError, ValueError):
            raise HTTPException(404) from None
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg")

    @api.get("/sessions/{session_id}/pages/{page}.png")
    async def manual_page(session_id: str, page: int) -> Response:
        """One manual page as an image, for the step card's 'show page'. Only a page the session's card cites; the
        PDF stays in the private folder and the image is never cached to disk."""
        s = reg.get(session_id)
        if s is None or s.card is None or page not in cited_pages(s.card):
            raise HTTPException(404)
        try:
            png = await asyncio.to_thread(render_page, s.card.brand, s.card.model, page,
                                          household=s.manual_household)
        except (FileNotFoundError, IndexError):
            raise HTTPException(404) from None
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    if store is not None:
        from app.api import household_routes

        api.include_router(household_routes(store, sessions_root=lambda: replays or config.sessions_dir(),
                                            load_card=_card, ingest=ingest, client=_client))
    app.include_router(api)
    app.state.unions = unions
    return app


def checked_card_loader(country: str = "nl") -> Callable[[str], Any]:
    """The schedule's CardLoader: a card path -> the card with its checks filled, or None when it does not load.
    Reminders come only from a checked card, so this fails closed like the session does. A card is read from the
    repo's cards/, or from FIXIT_PRIVATE/cards (built by ingest for a household's appliance) only when its check record
    is bound to the card's bytes and to the manual in the private folder (cards.private_record_failures)."""
    from app.ports import IngestManuals

    manuals = IngestManuals()
    cache: dict[tuple[str, float, float], Any] = {}

    def load(card_path: str) -> Any:
        path = (config.ROOT / card_path).resolve()
        priv = config.private_dir()
        private = priv is not None and (priv / "cards").resolve() in path.parents
        if not path.exists() or not (private or config.CARDS_DIR.resolve() in path.parents):
            return None
        if private and cards.private_record_failures(path):
            return None
        rec = path.with_suffix(".check.json")
        key = (str(path), path.stat().st_mtime, rec.stat().st_mtime if rec.exists() else 0.0)
        if key not in cache:
            loaded = cards.load(path, country, manuals)
            cache[key] = loaded.card if loaded.usable else None
        return cache[key]

    return load


def served_client() -> tuple[calls.Client, calls.Client | None]:
    """The served app's client (FIXIT_CALLS) and its local fallback. Recording, both write their fixtures to
    FIXIT_PRIVATE/fixtures, never the repo's fixtures/: a live session's replies quote the manual. The client also
    becomes the process's calls.default_client, so ingest's plate read and lookup (called without a client) record
    there too."""
    mode = calls.default_client().mode
    kw: dict[str, Any] = {}
    if mode == "record":
        kept = config.served_fixtures()
        if kept is None:
            raise RuntimeError("FIXIT_CALLS=record needs FIXIT_PRIVATE: the served app keeps its fixtures there")
        kw["fixtures_root"] = kept
    client = calls.make_client(mode, local=calls.local_mode(), **kw)
    calls._default = client  # noqa: SLF001  # the one served client, for every caller in this process
    fallback = None if calls.local_mode() else calls.make_client(mode, local=True, **kw)
    return client, fallback


def production_app(dist: Path | None = None) -> FastAPI:
    """The served app: /ws and its routes, the schedule's household routes and push timer over the store, the read-only
    HA client, and the phone's PWA at / (mounted last: its catch-all must lose to every other route)."""
    from contextlib import asynccontextmanager

    from app.feeds.router import make_router
    from app.feeds.service import feeds_deps, push_loop
    from app.ha.client import HA, prefix_map
    from app.schedule.invites import db_path
    from app.schedule.store import open_store
    from deploy.pwa import mount_pwa

    conn = open_store(db_path())
    deps = feeds_deps(conn, checked_card_loader(), ha_available=HA(prefix_map({})).available)
    client, fallback = served_client()
    app = create_app(client=client, store=Store(conn, deps.secret, getattr(client, "ledger", None)),
                     ha_for=lambda m: HA(prefix_map(m)), fallback=fallback)
    app.include_router(make_router(deps), prefix="/api/v1")

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        task = asyncio.create_task(push_loop(deps))
        yield
        task.cancel()

    app.router.lifespan_context = lifespan
    mount_pwa(app, dist or config.ROOT / "phone" / "dist")
    return app
