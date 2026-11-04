"""Adding an appliance (POST /appliances): a typed model or a plate photo goes to the ingest package in the background
(plate → lookup → fetch → ingest → done), and the appliance's card_status follows it:
pending → checked | pages_only | failed. A lookup miss is `failed` with ingest's notice (the maker's page); the user can
then upload the PDF (POST /appliances/{id}/manual) or post its link again under the same alias.

While it runs, what ingest knows (the manual's pages and title, the quotes checked, each job as its check passes) is
served on the appliance's `ingest`, with every title that no code wrote passed through G0 first (`served`).

Plate photos arrive over the WS (plate_photo) and are read there (app.plates); only the settled brand and model are
kept, by photo id in FIXIT_PRIVATE/plates, and the photo itself never: never in the repo, never in a card, never on
disk. A built card lives in FIXIT_PRIVATE/cards; only curated cards are in the repo's cards/.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import secrets
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import calls
import rules
from app import config, library, recorder
from app.feeds.caps import household_cap, household_lane
from app.store import Store
from ingest.lookup import maker_url_ok

Ingest = Callable[..., Awaitable[Any]]  # ingest.flow.ingest_appliance's shape
MAX_PDF_BYTES = 52_428_800  # POST /appliances/{id}/manual: 50 MB, as ingest's own check
STAGES = ("plate", "lookup", "fetch", "ingest", "done")  # Appliance.ingest.stage: the phone maps each to its words

# Makers whose terms forbid reading their manuals with software, or whose download host blocks crawlers: no
# card, shared or private, and no lookup or fetch, until the maker agrees. Ingest refuses them too, for a brand only
# the plate gives.
DECLINED = frozenset({"samsung"})
# Model codes shaped like those makers' own (Samsung: WW90T534DAW, DV90T5240AW, DW60M6050FS). With no brand typed, the
# add asks for the brand instead of guessing either way.
DECLINED_MODELS = re.compile(r"(WW|DV|DW)\d{2}[A-Z]")


def needs_brand(brand: str | None, model: str | None) -> bool:
    """A typed model with no brand that may be a declined maker's."""
    code = re.sub(r"[\s\-./]", "", (model or "").upper())
    return not (brand or "").strip() and DECLINED_MODELS.match(code) is not None

# Appliance.ingest.notice: fixed, code-written text only. The phone draws it outside G0, so nothing a model wrote and
# nothing from an error message goes in; ingest's notice gives only its kind, its reason code and the maker's page.
# The words name what the add screen offers: the maker's page (shown only for an https one), the Manual link field
# and the Add the PDF button.
NOTICES: dict[str, dict[str, str]] = {
    "lookup_failed": {"en": "I couldn't find the manual for this model. Open the maker's page and download its user "
                            "manual, then tap Add the PDF, or paste the manual's link under Manual link.",
                      "nl": "Ik kon de handleiding van dit model niet vinden. Open de pagina van de fabrikant en "
                            "download de gebruikershandleiding, tik dan op Pdf toevoegen, of plak de link hieronder."},
    "lookup_failed_no_page": {"en": "I couldn't find the manual for this model. Tap Add the PDF if you have it, or "
                                    "paste a link to it from the maker's own site under Manual link.",
                              "nl": "Ik kon de handleiding van dit model niet vinden. Tik op Pdf toevoegen als je hem "
                                    "hebt, of plak hieronder een link ernaartoe op de site van de fabrikant."},
    "ingest_failed": {"en": "I found the manual but couldn't make the repair card from it. Its pages are still "
                            "there to read.",
                      "nl": "Ik vond de handleiding, maar kon er geen reparatiekaart van maken. De pagina's zijn er "
                            "nog om te lezen."},
    "plate_unreadable": {"en": "I couldn't read the plate. Take a closer photo, or type the model code.",
                         "nl": "Ik kon het typeplaatje niet lezen. Maak een foto van dichterbij, of typ de modelcode."},
}


def maker_url(url: str) -> bool:
    """pdf_url is fetched only over https from a lookup maker's own domain (ingest's maker_url_ok, from
    contracts/lookup_makers.json); anything else is a 422 and never a fetch, so the server is not an open fetcher.
    On top of that check: no credentials in the link and no port but 443."""
    parts = urllib.parse.urlsplit(url)
    if parts.username or parts.password or parts.port not in (None, 443):
        return False
    return maker_url_ok(url)


# Words for a reason code (IngestNotice.reason) where the kind's own words would send the user the wrong way.
REASONS: dict[str, dict[str, str]] = {
    "maker_terms": {"en": "This maker's terms don't allow apps to read its manuals, so I can't make a repair card "
                          "for it. You can still read the manual on the maker's own site.",
                    "nl": "De voorwaarden van deze fabrikant staan niet toe dat apps zijn handleidingen lezen, dus ik "
                          "kan er geen reparatiekaart voor maken. Je kunt de handleiding wel lezen op de site van de "
                          "fabrikant."},
    "not_mine": {"en": "You said the card I found isn't for your appliance. Tap Add the PDF to add your own manual, or "
                       "paste its link under Manual link.",
                 "nl": "Je gaf aan dat de kaart die ik vond niet bij jouw apparaat hoort. Tik op Pdf toevoegen om je "
                       "eigen handleiding toe te voegen, of plak hieronder de link ernaartoe."},
    "site_blocked": {"en": "The maker's site doesn't let me download the manual. Open the maker's page, download "
                           "the user manual yourself, then tap Add the PDF.",
                     "nl": "De site van de fabrikant laat mij de handleiding niet downloaden. Open de pagina van de "
                           "fabrikant, download de gebruikershandleiding zelf en tik op Pdf toevoegen."},
    "too_large": {"en": "That file is over 50 MB, too big for me to read. Paste a link to the manual under Manual "
                        "link instead.",
                  "nl": "Dat bestand is groter dan 50 MB, te groot om te lezen. Plak hieronder liever een link naar "
                        "de handleiding."},
    "not_a_manual": {"en": "That file isn't a PDF manual. Tap Add the PDF and pick the manual, or paste a link to "
                           "it under Manual link.",
                     "nl": "Dat bestand is geen pdf-handleiding. Tik op Pdf toevoegen en kies de handleiding, of plak "
                           "hieronder een link ernaartoe."},
    "scanned_unreadable": {"en": "That PDF is a scan I can't read the text of. The maker's own PDF of the manual "
                                 "usually works.",
                           "nl": "Die pdf is een scan waarvan ik de tekst niet kan lezen. De pdf van de fabrikant zelf "
                                 "werkt meestal wel."},
    "wrong_language": {"en": "That manual isn't in Dutch or English, the languages I have safety rules for. Add the "
                             "Dutch or English manual if the maker has one.",
                       "nl": "Die handleiding is niet in het Nederlands of Engels, de talen waarvoor ik "
                             "veiligheidsregels heb. Voeg de Nederlandse of Engelse handleiding toe als die er is."},
    "timeout": {"en": "Reading the manual took too long. Try again in a minute.",
                "nl": "Het lezen van de handleiding duurde te lang. Probeer het over een minuut nog eens."},
    "cap_reached": {"en": "Reading this manual needs more than is left of this home's budget for today, so I didn't "
                          "make the repair card.",
                    "nl": "Deze handleiding lezen kost meer dan er over is van het budget van dit huis voor vandaag, "
                          "dus ik heb geen reparatiekaart gemaakt."},
}
REASON_CODES = frozenset({*REASONS, "no_manual", "build_failed"})  # the contract's enum; these two use the kind's


def notice(kind: str, maker_page: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """The notice the phone draws. A lookup miss without a maker's page (the phone shows the link only for an https
    one) does not tell the user to open it; a known reason gets its own words."""
    has_page = (maker_page or "").startswith("https://")
    words = kind if kind != "lookup_failed" or has_page else "lookup_failed_no_page"
    text = REASONS[reason] if reason in REASONS and (reason != "site_blocked" or has_page) else NOTICES[words]
    out = {"kind": kind, "text": text, "maker_page": maker_page}
    if reason in REASON_CODES:
        out["reason"] = reason
    return out


def _stage(s: str) -> str:
    return s if s in STAGES else "plate" if s in ("plate_confirm", "closer_photo") else "done"


def plates_dir(household: str | None = None) -> Path | None:
    """Where settled plate reads are kept: plates/homes/<household>/ for a household's own (so deleting the household
    deletes them), plates/ for a session with no household."""
    priv = config.private_dir()
    if priv is None:
        return None
    return priv / "plates" if household is None else priv / "plates" / "homes" / recorder.safe_id(household)


def keep_settled(photo_id: str, brand: str | None, model: str | None, household: str | None = None) -> None:
    """The plate as the user settled it over the WS (nothing uncertain left), so POST /appliances with the photo id
    uses those characters instead of reading the plate again."""
    d = plates_dir(household)
    if d is not None and brand and model:
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{recorder.safe_id(photo_id)}.json").write_text(json.dumps({"brand": brand, "model": model}))


def settled_plate(photo_id: str, household: str | None = None) -> dict[str, str] | None:
    """Only the caller's own household's: a photo id never reads another home's plate."""
    d = plates_dir(household)
    p = None if d is None else d / f"{recorder.safe_id(photo_id)}.json"
    return json.loads(p.read_text()) if p is not None and p.exists() else None  # type: ignore[no-any-return]


def shared_card(brand: str, model: str, household: str | None = None) -> tuple[str, str, Path] | None:
    """The brand and model, as its card spells them, and the file of the card this model already has (the home's
    own first). A PDF or a link one household sends for a model with a card links that card instead of building
    another, unless the home said that card is not its appliance."""
    from ingest.cards import read_card
    from ingest.flow import find_card  # lazy, as below

    try:
        path = find_card(brand.lower(), model, household) or find_card(brand.lower(), model.upper(), household)
        card = read_card(path) if path is not None else None
    except Exception:  # an unreadable card on disk is not a checked one
        return None
    return (card["brand"], card["model"], path) if card and path else None


def _default_ingest() -> Ingest:
    from ingest.flow import ingest_appliance  # lazy, so the app starts without ingest's imports

    return ingest_appliance


@dataclass
class Progress:
    stage: str
    notice: dict[str, Any] | None = None
    alias_from_kind: bool = False  # the user gave no name: the alias follows the card's kind once it is known
    fields: dict[str, Any] = field(default_factory=dict)  # the Ingest progress fields, as served (after G0)

    def ingest(self) -> dict[str, Any]:
        """The contract's Ingest object."""
        return {"stage": self.stage, "notice": self.notice, **self.fields}


def _held(text: str, lang: str, country: str) -> rules.Hold | None:
    r = rules.check_sentence(text, lang, None, None, country=country)
    return r if isinstance(r, rules.Hold) else None


def _refusal(rule_id: str) -> str:
    family = rule_id.split(".")[1] if rule_id.count(".") >= 1 else ""
    return family if family in ("gas", "mains") else "other"


def served(update: dict[str, Any], country: str, appliance_id: str) -> dict[str, Any]:
    """Ingest's progress fields as the phone may see them. The manual's title (the PDF's own metadata) and each
    job's title (model-written) are untrusted: a title G0 holds in any of the country's languages is never served.
    A held manual title is null; a job title is kept in each language it passes in, and a job with none left is
    listed in jobs_refused by its id and the rule's family only."""
    c = rules.load_country(country)
    langs = list(c.languages) if c is not None else ["en"]
    out = {k: v for k, v in update.items() if k in ("progress", "elapsed_s", "eta_s", "quotes")}
    if isinstance(update.get("manual"), dict):
        manual = dict(update["manual"])
        title = manual.get("title")
        if title and any(_held(title, lang, country) for lang in langs):
            manual["title"] = None
        manual["thumb_url"] = f"/api/v1/appliances/{appliance_id}/manual/pages/1.png"
        out["manual"] = manual
    ready, refused = [], []
    for job in update.get("jobs_ready") or []:
        title: dict[str, str] = {}
        hold = None
        for lang, text in (job.get("title") or {}).items():
            h = _held(text, lang, country) if lang in langs else None
            if h is None and lang in langs:
                title[lang] = text
            hold = hold or h
        if title:
            ready.append({**job, "title": title})
        else:
            refused.append({"id": job["id"], "reason": _refusal(hold.rule_id) if hold else "other"})
    if "jobs_ready" in update:
        out["jobs_ready"] = ready
    if refused:
        out["jobs_refused"] = refused
    return out


@dataclass
class Adder:
    store: Store
    ingest: Ingest | None = None
    progress: dict[str, Progress] = field(default_factory=dict)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)
    _homes: dict[asyncio.Task[None], str] = field(default_factory=dict)  # each running ingest's household
    loop: asyncio.AbstractEventLoop | None = None

    async def end_household(self, household_id: str) -> dict[str, int]:
        """A deleted household's running ingests are cancelled, so none writes a manual or a card after the sweep."""
        mine = [t for t, h in self._homes.items() if h == household_id and not t.done()]
        for t in mine:
            t.cancel()
        for t in mine:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        return {"ingests": len(mine)}

    def start(self, household: dict[str, Any], body: dict[str, Any], existing: str | None) -> str:
        """Write the appliance row (or reuse the alias's) and start its ingest. Returns the appliance id."""
        conn = self.store.conn
        typed = bool(body.get("model"))
        kind = body.get("kind") or "appliance"
        if existing is None:
            aid = "ap_" + secrets.token_hex(6)
            alias = body.get("alias") or self.free_alias(household["id"], kind)
            with conn:
                conn.execute(
                    "INSERT INTO appliances (id, household_id, alias, kind, brand, model, added_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (aid, household["id"], alias, kind, (body.get("brand") or "").lower(), body.get("model") or "",
                     "typed" if typed or not body.get("plate_photo_id") else "plate", _now()))
        else:
            aid = existing
            with conn:
                conn.execute("DELETE FROM cards_ref WHERE appliance_id = ?", (aid,))  # pending again
        first = "fetch" if body.get("pdf") is not None else "lookup" if typed else "plate"  # an upload has its manual
        self.progress[aid] = Progress(first, alias_from_kind=not body.get("alias"))
        task = asyncio.ensure_future(self._run(aid, household, body))
        self.loop = asyncio.get_running_loop()
        self._tasks.add(task)
        self._homes[task] = household["id"]
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda t: self._homes.pop(t, None))
        return aid

    async def _run(self, aid: str, household: dict[str, Any], body: dict[str, Any]) -> None:
        prog = self.progress[aid]

        def on_progress(stage: str, detail: str = "") -> None:
            prog.stage = _stage(stage)

        def on_update(update: dict[str, Any]) -> None:
            prog.fields = served(update, household["country"], aid)

        brand, model = body.get("brand"), body.get("model")
        settled = settled_plate(body["plate_photo_id"], household["id"]) \
            if body.get("plate_photo_id") and not model else None
        if settled is not None:  # the user confirmed the plate's characters: a typed brand and model win in ingest
            brand, model = settled["brand"], settled["model"]
        if (brand or "").strip().lower() in DECLINED:  # before any lookup, fetch, write or model call
            prog.stage, prog.notice = "done", notice("lookup_failed", reason="maker_terms")
            self._card_ref(aid, None, "failed")
            return
        pdf, pdf_url = body.get("pdf"), body.get("pdf_url")
        known = shared_card(brand, model, household["id"]) if (pdf is not None or pdf_url) and brand and model else None
        if known is not None and not library.unlinked(self.store.conn, aid, known[2]):
            brand, model = known[0], known[1]  # the model's card already exists: ingest links it, never rebuilds it
            pdf = pdf_url = None
        try:
            res = await (self.ingest or _default_ingest())(
                kind=body.get("kind"), brand=brand, model=model,
                pdf=pdf, pdf_url=pdf_url, country=household["country"],
                lane=household_lane(self.store.conn, household["id"], config.LANE),
                household=household["id"], household_cap_usd=household_cap(self.store.conn, household["id"]),
                on_progress=on_progress, on_update=on_update, household_lang=household.get("lang"),
                **({"build": False} if body.get("from_ha") else {}))  # an add from Home Assistant builds nothing
        except calls.BudgetExceeded:  # the home's (or the run's) budget for today: every call of an ingest counts
            prog.stage, prog.notice = "done", notice("ingest_failed", reason="cap_reached")
            self._card_ref(aid, None, "failed")
            return
        except Exception:  # ingest talks to the network; a crash is a failed add, never a 500
            prog.stage, prog.notice = "done", notice("ingest_failed", reason="build_failed")
            self._card_ref(aid, None, "failed")
            return
        prog.stage = _stage(res.stage)
        kind = (res.card or {}).get("appliance_kind")
        if res.notice and res.notice.get("kind") in NOTICES:
            prog.notice = notice(res.notice["kind"], res.notice.get("maker_page"), res.notice.get("reason"))
        with self.store.conn:
            self.store.conn.execute(
                "UPDATE appliances SET brand = COALESCE(?, brand), model = COALESCE(?, model), plate_confirmed = ?, "
                "kind = COALESCE(?, kind) WHERE id = ?",
                (res.brand, res.model, int(bool(res.plate_confirmed)), kind, aid))
            if kind and prog.alias_from_kind:
                self.store.conn.execute("UPDATE appliances SET alias = ? WHERE id = ? AND deleted_at IS NULL",
                                        (self.free_alias(household["id"], kind, aid), aid))
        card_path, status = res.card_path, res.status
        if card_path is not None and status == "checked":  # shared only as the library allows (K1)
            card_path = library.link(self.store.conn, aid, household["id"], Path(card_path), res.model or model or "")
            if card_path is None:  # this home said that card is not its appliance
                status, prog.notice = "failed", notice("lookup_failed", reason="not_mine")
        if status != "pending":
            self._card_ref(aid, card_path, status)

    def free_alias(self, household_id: str, kind: str, own: str | None = None) -> str:
        """The kind as a name, numbered when the household already has one ('washer', 'washer 2')."""
        taken = {r["alias"] for r in self.store.conn.execute(
            "SELECT alias FROM appliances WHERE household_id = ? AND id != ?", (household_id, own or ""))}
        base = kind.replace("_", " ")[:36]
        n = 1
        while (alias := base if n == 1 else f"{base} {n}") in taken:
            n += 1
        return alias

    def _card_ref(self, aid: str, card_path: Path | None, status: str) -> None:
        rel, sha = "", ""
        if card_path is not None and Path(card_path).exists():
            p = Path(card_path).resolve()
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            rel = str(p.relative_to(config.ROOT)) if config.ROOT.resolve() in p.parents else str(p)
        with self.store.conn:
            self.store.conn.execute(
                "INSERT OR REPLACE INTO cards_ref (appliance_id, card_path, card_sha256, status, checked_at) "
                "VALUES (?, ?, ?, ?, ?)", (aid, rel, sha, status, _now() if status == "checked" else None))

    async def idle(self) -> None:
        """Wait for every running ingest (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
