"""The plate, over the WS (before POST /appliances): plate_photo → ingest's plate read, from the bytes in memory (the
serial is never read) → plate_read with the uncertain characters; the user's confirm_plate tap settles them → a
plate_read with nothing uncertain. Only a settled read's brand and model are kept (FIXIT_PRIVATE/plates); the photo is
never written, and the read's recorded request body and response (record mode) are dropped once it is in, so no copy
of the plate stays. The phone then posts /appliances with the photo id, which finds the settled brand and model. A
re-run plays the recorded plate_read back and makes no call. The plate's fields live in the session only; never in a
card or the repo.
"""

from __future__ import annotations

import base64
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import calls
from app import adding, trace
from calls.fixtures import request_sha256
from contracts.models import ws

if TYPE_CHECKING:
    from app.session import Session

PLATE = {"ok": "read it", "amber": "read it, some characters unsure", "pattern": "read it, some characters unsure",
         "too_uncertain": "too unsure to use", "no_plate": "no plate in the photo", "unreadable": "couldn't read it"}
Reader = Callable[..., Awaitable[Any]]  # ingest.plate.read_plate's shape


def _default_reader() -> Reader:
    from ingest.plate import read_plate

    return read_plate


def _known_models() -> list[str]:
    from ingest.flow import known_models  # the card models and aliases the plate read fits to

    return known_models()


class Noted:
    """The client the plate read goes through: notes the request hash of each body it sends (both attempts of a
    retry), so the bodies and responses record mode kept can be dropped once the read is in."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.shas: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    async def respond(self, role: str, input: Any, **kw: Any) -> Any:
        build = getattr(self.client, "build_respond_body", None)
        if build is not None:
            keys = ("tools", "format", "effort", "images", "instructions", "max_output_tokens")
            for attempt in (1, 2):
                self.shas.append(request_sha256(build(role, input, attempt=attempt,
                                                      **{k: kw[k] for k in keys if k in kw})))
        return await self.client.respond(role, input, **kw)


def forget(client: Any, shas: list[str]) -> int:
    """Drop what record mode kept of these calls: the request body (it holds the photo) and the response (it holds
    what the plate says). The ledger keeps only their hashes. Returns how many files went."""
    if getattr(client, "mode", None) != "record":
        return 0
    gone = 0
    priv, root = getattr(client, "private", None), getattr(client, "fixtures_root", None)
    for sha in shas:
        paths = ([priv / "requests" / f"{sha}.json"] if priv is not None else []) + \
            (list(root.glob(f"*/{sha}.jsonl")) if root is not None and root.is_dir() else [])
        for p in paths:
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
                gone += 1
    return gone


def recorded(s: Session, photo_id: str) -> list[dict[str, Any]]:
    """What the live session sent for this photo, from its record: the plate rail row, any notice, and the
    plate_read, in order."""
    lines = s.rerun or []
    at = next((i for i, x in enumerate(lines) if x.get("dir") == "c2s" and x.get("kind") == "plate_photo"
               and (x.get("msg") or {}).get("photo_id") == photo_id), None)
    out: list[dict[str, Any]] = []
    for x in lines[at + 1:] if at is not None else []:
        m = x.get("msg") or {}
        if x.get("dir") != "s2c":
            continue
        if (m.get("type") == "rail" and m.get("tool") == "plate") or m.get("type") == "notice":
            out.append(m)
        elif m.get("type") == "plate_read" and m.get("photo_id") == photo_id:
            out.append(m)
            return out
    return []


async def replay_read(s: Session, photo_id: str) -> None:
    """A re-run: the recorded rail row, notices and plate_read again, with no call (no photo was kept to read)."""
    msgs = recorded(s, photo_id)
    if not msgs:
        await s._no_recording()
        await s.emit({"type": "plate_read", "photo_id": photo_id, "brand": None, "model": None, "fields": []})
        return
    for m in msgs:
        await s.emit({k: v for k, v in m.items() if k not in ("seq", "t_ms")})
    read = msgs[-1]
    s.plates[photo_id] = {"brand": read.get("brand"), "model": read.get("model"), "fields": read.get("fields", [])}


async def on_plate_photo(s: Session, msg: ws.PlatePhoto, reader: Reader | None = None) -> None:
    if s.models.rerunning:
        await replay_read(s, msg.photo_id)
        return
    jpeg = base64.b64decode(msg.jpeg)
    client = Noted(s.client)
    try:
        card = await (reader or _default_reader())(jpeg, lane=s.models.lane, household=s.household,
                                                    session_id=s.session_id, known_models=_known_models(),
                                                    client=client)
    except (calls.CallError, ValueError) as e:
        forget(s.client, client.shas)
        await s.rail("plate", "", trace.failed(e) if isinstance(e, calls.CallError) else "couldn't read the plate", 0,
                     photo_id=msg.photo_id, error=type(e).__name__)
        if isinstance(e, calls.MissingFixture):  # a replay without this recording (ingest calls the client itself)
            await s._no_recording()
        else:
            await s.notice("slow_connection" if isinstance(e, calls.CallTimeout) else "lookup_failed")
        await s.emit({"type": "plate_read", "photo_id": msg.photo_id, "brand": None, "model": None, "fields": []})
        return
    forget(s.client, client.shas)
    fields = card.ws_fields()
    s.plates[msg.photo_id] = {"brand": card.brand, "model": card.model, "fields": fields}
    if not any(f["uncertain"] for f in fields):
        adding.keep_settled(msg.photo_id, card.brand, card.model, s.household)
    await s.rail("plate", "", PLATE.get(card.reason, "read it"), 0, photo_id=msg.photo_id, reason=card.reason)
    await s.emit({"type": "plate_read", "photo_id": msg.photo_id, "brand": card.brand, "model": card.model,
                  "fields": fields})


def settle(read: dict[str, Any], chosen: dict[str, list[str]]) -> dict[str, Any]:
    """The plate read with the user's choices put in: each uncertain character replaced by the option picked for it
    (only one of its options). A character with no valid pick stays uncertain, so the phone asks again."""
    fields = []
    for f in read["fields"]:
        chars = list(f["value"])
        picks = chosen.get(f["name"], [])
        left = []
        for i, u in enumerate(f["uncertain"]):
            pick = picks[i] if i < len(picks) else None
            if pick in u["options"] and 0 <= u["index"] < len(chars):
                chars[u["index"]] = pick
            else:
                left.append(u)
        fields.append({"name": f["name"], "value": "".join(chars), "uncertain": left})
    model = next((f["value"] for f in fields if f["name"] == "model"), read["model"])
    brand = next((f["value"] for f in fields if f["name"] == "brand"), read["brand"])
    return {"brand": brand, "model": model, "fields": fields}


async def on_confirm(s: Session, arg: str | None) -> None:
    photo_id, read = next(reversed(s.plates.items()), (None, None))
    if photo_id is None or read is None:
        return
    try:
        chosen = json.loads(arg or "{}")
        assert isinstance(chosen, dict)
    except (ValueError, AssertionError):
        return
    settled = settle(read, {k: [str(x) for x in v] for k, v in chosen.items() if isinstance(v, list)})
    s.plates[photo_id] = settled
    if not any(f["uncertain"] for f in settled["fields"]):
        adding.keep_settled(photo_id, settled["brand"], settled["model"], s.household)
    await s.emit({"type": "plate_read", "photo_id": photo_id, **settled})
