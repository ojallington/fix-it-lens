"""The household routes (api.openapi.yaml): the appliances, their cards and what a fault code means, history, sessions
and the technician note.
The schedule router (app/schedule) serves households, due dates, feeds, push and preflight; both sit under /api/v1 and
use the same bearer token. Every route here is the household's own: another household's id is a 404, never a 403.
"""

# no `from __future__ import annotations`: FastAPI reads the local Household alias at runtime

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import calls
from app import cards, config, display, erase, faults, library, recorder
from app.adding import MAX_PDF_BYTES, Adder, Ingest, maker_url, needs_brand, notice
from app.note import build_note
from app.ports import manual_household
from app.store import Store
from contracts.models.card import Card

UserKind = Literal["voice_note", "user_entry", "fault"]  # job_done and stop are written by code, never posted


class HistoryIn(BaseModel):
    kind: UserKind
    on_date: date
    appliance_id: str | None = None
    job_id: str | None = None
    session_id: str | None = None
    text: str | None = Field(default=None, max_length=2000)


class ApplianceIn(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=40)  # default: the card's kind
    kind: str | None = Field(default=None, min_length=1, max_length=40)  # default: from the plate and the card
    brand: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=60)
    plate_photo_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,120}$")
    pdf_url: str | None = Field(default=None, max_length=500, pattern=r"^https://")  # after a lookup miss


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["wrong_step", "wrong_page", "unsafe", "other"]
    note: str | None = Field(default=None, min_length=1, max_length=500)
    job_id: str | None = Field(default=None, max_length=120)
    step_id: str | None = Field(default=None, max_length=120)
    page: int | None = Field(default=None, ge=1)


class NotMineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, min_length=1, max_length=500)


class NoteIn(BaseModel):
    photo_frame_ids: list[str] = Field(max_length=12)
    renter: bool = False


def cap_error(why: str, now: datetime | None = None) -> dict[str, Any]:
    """The contract's CapError for a refused call: the household's own cap reopens at UTC midnight; a lane or the
    total has no known reopening time."""
    if why.startswith("household"):
        now = now or datetime.now(UTC)
        midnight = datetime.combine(now.date() + timedelta(days=1), time(0), UTC)
        return {"error": "home_cap", "reopens_at": midnight.isoformat().replace("+00:00", "Z")}
    return {"error": "global_cap", "reopens_at": None}


def household_routes(store: Store, *, sessions_root: Callable[[], Path],
                     load_card: Callable[[str], cards.Loaded | None], ingest: Ingest | None = None,
                     client: Callable[[], calls.Client] | None = None) -> APIRouter:
    r = APIRouter()
    adder = Adder(store, ingest)
    erase.watch(adder)  # a deleted household's running ingests stop before its files go
    r.adder = adder  # type: ignore[attr-defined]  # tests wait on it

    def household(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        """The household's own token or a person's app token (a phone back from a return link), for every route here;
        a view-only person only reads (403 on a write). A token the household issued to its agent or its Home
        Assistant only reads: its writes go through the schedule routes."""
        from app.schedule.store import caller_from_token

        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "household token required")
        c = caller_from_token(store.conn, store.secret, authorization[7:].strip())
        reads = request.method == "GET"
        who = None if c is None else store.role(c)
        if c is None or (who is None if c.by_kind == "app" else c.by_kind not in ("agent", "ha") or not reads):
            raise HTTPException(401, "bad household token")
        if who == "viewer" and not reads:
            raise HTTPException(403, {"error": "forbidden", "detail": "this person can view but not change anything"})
        return dict(c.household)

    Household = Annotated[dict[str, Any], Depends(household)]

    def adder_caller(request: Request, authorization: Annotated[str | None, Header()] = None) -> tuple[dict[str, Any],
                                                                                                     bool]:
        """POST /appliances: everyone `household` lets write, and also the home's Home Assistant with a write token,
        for this one add (its discovery list). Returns the household and whether Home Assistant is adding."""
        from app.schedule.store import caller_from_token

        token = (authorization or "")[7:].strip() if (authorization or "").lower().startswith("bearer ") else ""
        c = caller_from_token(store.conn, store.secret, token) if token else None
        if c is None or c.by_kind != "ha":
            return household(request, authorization), False
        if c.scope != "write":
            raise HTTPException(403, {"error": "forbidden", "detail": "a read-only Home Assistant token cannot add"})
        return dict(c.household), True

    AddingAs = Annotated[tuple[dict[str, Any], bool], Depends(adder_caller)]

    def caller(authorization: Annotated[str | None, Header()] = None) -> Any:
        """Who is calling: the household's own token (its first admin), a person's app token, or a token the home
        issued to its agent, its Home Assistant or a calendar."""
        from app.schedule.store import caller_from_token

        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "household token required")
        c = caller_from_token(store.conn, store.secret, authorization[7:].strip())
        if c is None:
            raise HTTPException(401, "bad household token")
        return c

    def person(c: Annotated[Any, Depends(caller)]) -> Any:
        if store.role(c) is None:
            raise HTTPException(403, {"error": "forbidden", "detail": "a person's word, not an agent, ha or feed"})
        return c

    def member(c: Annotated[Any, Depends(caller)]) -> Any:
        if store.role(c) not in ("admin", "member"):
            raise HTTPException(403, {"error": "forbidden", "detail": "an admin or a member only"})
        return c

    Person = Annotated[Any, Depends(person)]
    Member = Annotated[Any, Depends(member)]

    def too_many() -> HTTPException:
        return HTTPException(429, {"error": "too_many_reports",
                                   "detail": f"this home has {library.OPEN_REPORTS_MAX} open reports"})

    @r.post("/appliances/{appliance_id}/reports", status_code=201)
    def file_report(appliance_id: str, body: ReportIn, c: Person) -> dict[str, Any]:
        """A person's report on the linked card, or one of its jobs or steps. The card never changes."""
        appliance({"id": c.household_id}, appliance_id)
        if body.kind == "wrong_step" and not body.step_id or body.kind == "wrong_page" and body.page is None \
                or body.kind == "other" and not body.note:
            raise HTTPException(422, {"error": "invalid", "detail": f"{body.kind} needs its step, page or note"})
        ln = library.linked(store.conn, appliance_id)
        if ln is None:
            raise HTTPException(409, {"error": "no_card", "detail": "nothing is linked to report on"})
        try:
            raw = cards.read(library.resolve(ln["card_path"]))
        except Exception:  # a card that no longer reads has no jobs to name
            raw = None
        jobs = {j.id: j for j in (raw.jobs if raw is not None else [])}  # refused jobs too: they can be reported
        if body.job_id is not None and body.job_id not in jobs:
            raise HTTPException(422, {"error": "unknown_job", "detail": "no such job on the linked card"})
        steps = {st.id for j in ([jobs[body.job_id]] if body.job_id else jobs.values()) for st in j.steps}
        if body.step_id is not None and body.step_id not in steps:
            raise HTTPException(422, {"error": "unknown_step", "detail": "no such step on the linked card"})
        try:
            out = library.report(store.conn, household_id=c.household_id, appliance_id=appliance_id, kind=body.kind,
                                 by_person=c.person_id, by_label=c.label, note=body.note, job_id=body.job_id,
                                 step_id=body.step_id, page=body.page)
        except library.TooMany:
            raise too_many() from None
        if out is None:
            raise HTTPException(409, {"error": "no_card", "detail": "nothing is linked to report on"})
        return out

    @r.get("/reports")
    def list_reports(hh: Household, appliance_id: str | None = None) -> list[dict[str, Any]]:
        return library.reports(store.conn, hh["id"], appliance_id)

    @r.post("/appliances/{appliance_id}/not-mine")
    def not_mine(appliance_id: str, c: Member, body: NotMineIn | None = None) -> dict[str, Any]:
        """"This isn't my appliance": unlink the card for this home only; the upload or manual link path takes over."""
        appliance({"id": c.household_id}, appliance_id)
        try:
            done = library.not_mine(store.conn, household_id=c.household_id, appliance_id=appliance_id,
                                    by_person=c.person_id, by_label=c.label, note=body.note if body else None)
        except library.TooMany:
            raise too_many() from None
        if not done:
            raise HTTPException(409, {"error": "no_card", "detail": "nothing is linked"})
        adder.progress.pop(appliance_id, None)
        return with_progress(appliance({"id": c.household_id}, appliance_id))

    @r.get("/appliances/{appliance_id}/card/record")
    def card_record(appliance_id: str, hh: Household) -> dict[str, Any]:
        appliance(hh, appliance_id)
        out = library.record(store.conn, appliance_id, hh["id"])
        if out is None:
            raise HTTPException(404, "no card linked")
        return out

    def appliance(hh: dict[str, Any], appliance_id: str) -> dict[str, Any]:
        found = store.appliances(hh["id"], appliance_id)
        if not found:
            raise HTTPException(404, "no such appliance")
        return found[0]

    @r.get("/appliances")
    def list_appliances(hh: Household) -> list[dict[str, Any]]:
        return store.appliances(hh["id"])

    def with_progress(a: dict[str, Any]) -> dict[str, Any]:
        prog = adder.progress.get(a["id"])
        if prog is not None and (a["card_status"] != "checked" or prog.notice):
            a = {**a, "ingest": prog.ingest()}
        elif prog is None and a["card_status"] == "failed" and library.said_not_mine(store.conn, a["id"]):
            a = {**a, "ingest": {"stage": "done", "notice": notice("lookup_failed", reason="not_mine")}}
        return a

    @r.post("/appliances", status_code=201)
    async def add_appliance(body: ApplianceIn, who: AddingAs) -> dict[str, Any]:
        hh, from_ha = who
        if from_ha and (body.plate_photo_id or body.pdf_url):
            raise HTTPException(422, {"error": "invalid", "detail": "Home Assistant adds a brand and model only"})
        if body.pdf_url is not None and not maker_url(body.pdf_url):
            raise HTTPException(422, {"error": "invalid", "detail": "pdf_url must be on a lookup maker's own site"})
        same = [a for a in store.appliances(hh["id"]) if body.alias and a["alias"] == body.alias]
        data = body.model_dump()
        if same and body.pdf_url and not (body.model or body.plate_photo_id):  # the manual's link after a miss
            data["brand"], data["model"] = same[0]["brand"] or None, same[0]["model"] or None
        if not data["model"] and not body.plate_photo_id:
            raise HTTPException(422, {"error": "invalid", "detail": "a model code or a plate photo"})
        if needs_brand(data["brand"], data["model"]):
            raise HTTPException(422, {"error": "brand_needed", "detail": "type the brand as well as the model code"})
        if same and same[0]["card_status"] == "checked":
            raise HTTPException(409, {"error": "alias_taken", "detail": f"{body.alias} already has its card"})
        if from_ha:
            data["from_ha"] = True  # the library's card links at once; with none, the manual's pages and no build
        aid = adder.start(hh, data, same[0]["id"] if same else None)
        return with_progress(appliance(hh, aid))

    @r.post("/appliances/{appliance_id}/manual", status_code=201)
    async def upload_manual(appliance_id: str, hh: Household, request: Request) -> dict[str, Any]:
        """The manual's PDF from the phone (the file picker, or the Android share target): the ingest runs again on
        these bytes, as a pdf_url re-post does. The size and the type are checked before a byte is kept."""
        a = appliance(hh, appliance_id)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/pdf":
            raise HTTPException(415, {"error": "not_a_pdf", "detail": "send the manual as application/pdf"})
        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > MAX_PDF_BYTES:
            raise HTTPException(413, {"error": "too_large", "detail": "the manual is over 50 MB"})
        pdf = bytearray()
        async for chunk in request.stream():
            pdf += chunk
            if len(pdf) > MAX_PDF_BYTES:
                raise HTTPException(413, {"error": "too_large", "detail": "the manual is over 50 MB"})
        if not pdf.startswith(b"%PDF"):
            raise HTTPException(415, {"error": "not_a_pdf", "detail": "the file is not a PDF"})
        adder.start(hh, {"alias": a["alias"], "kind": a["kind"], "brand": a["brand"] or None,
                         "model": a["model"] or None, "pdf": bytes(pdf)}, appliance_id)
        return with_progress(appliance(hh, appliance_id))

    @r.get("/appliances/{appliance_id}/manual/pages/{page}.png")
    async def manual_page(appliance_id: str, page: int, hh: Household) -> Response:
        """One page of the manual this household's appliance was built from, for the wait screen's thumbnail. The
        PDF stays in the private folder; the image is rendered on request and cached by the phone only."""
        from app.server import render_page

        a = appliance(hh, appliance_id)
        if not (a["brand"] and a["model"]) or page < 1:
            raise HTTPException(404, "no manual yet")
        ref = store.appliance(hh["id"], appliance_id)
        owner = manual_household(ref.card_path) if ref is not None and ref.card_path else hh["id"]  # pages only: own
        try:
            png = await asyncio.to_thread(render_page, a["brand"], a["model"], page, household=owner)
        except (FileNotFoundError, IndexError):
            raise HTTPException(404, "no such page") from None
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @r.get("/appliances/{appliance_id}")
    def get_appliance(appliance_id: str, hh: Household) -> dict[str, Any]:
        return with_progress(appliance(hh, appliance_id))

    @r.delete("/appliances/{appliance_id}", status_code=204)
    def delete_appliance(appliance_id: str, hh: Household) -> Response:
        """Takes the appliance out of this home (a dead add, or one the home no longer has). Another home's id is a
        404; history and the card library stay."""
        if not store.remove_appliance(hh["id"], appliance_id):
            raise HTTPException(404, "no such appliance")
        adder.progress.pop(appliance_id, None)
        return Response(status_code=204)

    @r.get("/appliances/{appliance_id}/card")
    def get_card(appliance_id: str, hh: Household) -> dict[str, Any]:
        appliance(hh, appliance_id)
        ref = store.appliance(hh["id"], appliance_id)
        loaded = load_card(ref.card_path) if ref is not None and ref.card_path else None
        if loaded is None:
            raise HTTPException(404, "no card yet")
        out: dict[str, Any] = json.loads(loaded.card.model_dump_json(exclude_none=True))
        return out

    @r.get("/appliances/{appliance_id}/meaning")
    def get_meaning(appliance_id: str, hh: Household,
                    code: Annotated[str, Query(min_length=1, max_length=faults.MAX_CODE)]) -> dict[str, Any]:
        """A fault code as typed -> the card's fix job for it, or why there is none (app.faults)."""
        loaded, raw = checked(hh, appliance_id)
        if not code.strip():
            raise HTTPException(422, {"error": "invalid", "detail": "code: empty"})
        return faults.meaning(loaded.card, raw, code, hh.get("lang"), renter=bool(hh.get("renter")),
                              country=hh.get("country") or "nl", drawn=drawn_of(hh, appliance_id))

    def drawn_of(hh: dict[str, Any], appliance_id: str) -> faults.Drawn:
        ref = store.appliance(hh["id"], appliance_id)
        return faults.drawn_unconfirmed((config.ROOT / ref.card_path).resolve()) if ref and ref.card_path \
            else frozenset()

    def checked(hh: dict[str, Any], appliance_id: str) -> tuple[cards.Loaded, Card | None]:
        """The appliance's checked card, and the card as written (for what the rules took out of it)."""
        appliance(hh, appliance_id)
        ref = store.appliance(hh["id"], appliance_id)
        loaded = load_card(ref.card_path) if ref is not None and ref.card_path else None
        if ref is None or ref.card_path is None or loaded is None:
            raise HTTPException(404, "no card yet")
        try:
            raw = cards.read((config.ROOT / ref.card_path).resolve())
        except (OSError, ValueError):
            raw = None
        return loaded, raw

    @r.post("/appliances/{appliance_id}/display", response_model=None)
    async def read_display(appliance_id: str, hh: Household, request: Request) -> dict[str, Any] | JSONResponse:
        """One photo of the display or the lights -> what the card says it means (app.display)."""
        from app.feeds.caps import assert_household_under_cap, household_cap, household_lane

        loaded, raw = checked(hh, appliance_id)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "image/jpeg":
            raise HTTPException(415, {"error": "not_an_image", "detail": "send the photo as image/jpeg"})
        if int(request.headers.get("content-length") or 0) > display.MAX_BYTES:
            raise HTTPException(413, {"error": "too_large", "detail": "the photo is over 8 MB"})
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > display.MAX_BYTES:
                raise HTTPException(413, {"error": "too_large", "detail": "the photo is over 8 MB"})
        if not body.startswith(b"\xff\xd8\xff"):
            raise HTTPException(415, {"error": "not_an_image", "detail": "the file is not a JPEG"})
        try:
            cap = assert_household_under_cap(store.conn, store.ledger, hh["id"]) if store.ledger is not None \
                else household_cap(store.conn, hh["id"])
            display.keep(bytes(body), hh["id"])
            got = await display.read(bytes(body), loaded.card, client=(client or calls.default_client)(),
                                     lane=household_lane(store.conn, hh["id"], config.LANE), household=hh["id"],
                                     household_cap_usd=cap)
        except calls.BudgetExceeded as e:
            return JSONResponse(cap_error(str(e)), 429)
        return display.answer(loaded.card, raw, got, hh.get("lang"), renter=bool(hh.get("renter")),
                              country=hh.get("country") or "nl", drawn=drawn_of(hh, appliance_id))

    @r.get("/history")
    def get_history(hh: Household, appliance_id: str | None = None) -> list[dict[str, Any]]:
        return store.history(hh["id"], appliance_id)

    @r.post("/history", status_code=201)
    def post_history(body: HistoryIn, hh: Household) -> dict[str, Any]:
        if body.appliance_id is not None:
            appliance(hh, body.appliance_id)
        if body.session_id is not None and not store.sessions(hh["id"], body.session_id):
            raise HTTPException(404, "no such session")
        return store.add_history(hh["id"], {**body.model_dump(), "on_date": body.on_date.isoformat()})

    @r.get("/sessions")
    def list_sessions(hh: Household) -> list[dict[str, Any]]:
        return store.sessions(hh["id"])

    @r.get("/sessions/{session_id}")
    def get_session(session_id: str, hh: Household) -> dict[str, Any]:
        found = store.sessions(hh["id"], session_id)
        if not found:
            raise HTTPException(404, "no such session")
        return found[0]

    @r.post("/sessions/{session_id}/note")
    def write_note(session_id: str, body: NoteIn, hh: Household) -> dict[str, Any]:
        found = store.sessions(hh["id"], session_id)
        if not found:
            raise HTTPException(404, "no such session")
        try:
            rec = recorder.load(sessions_root(), recorder.safe_id(session_id))
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "no record of that session") from None
        loaded = load_card(rec.meta.card) if rec.meta.card else None
        aid = found[0]["appliance_id"]
        ref = store.appliance(hh["id"], aid) if aid else None
        history = [e for e in store.history(hh["id"], aid) if e["kind"] in ("fault", "stop", "voice_note")] \
            if aid else []
        note = build_note(rec, loaded.card if loaded is not None else None, photo_frame_ids=body.photo_frame_ids,
                          renter=body.renter, history=history[:20], alias=ref.alias if ref else None)
        note.pop("_held", None)
        return note

    return r
