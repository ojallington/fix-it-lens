"""Adding an appliance, end to end, with every failure said on screen: a failed manual lookup asks for the PDF;
a failed ingest keeps the manual as plain pages with no card and says so.

    out = await add_appliance("bosch", "SMV4HAX40N")                 # a card in cards/ or the private cards
    out = await add_appliance("siemens", "WM14N0A1")                 # lookup → fetch → ingest
    out = await add_appliance("bosch", "X", pdf=uploaded_bytes)      # the person shared the PDF
    out = await add_appliance("bosch", "X", pdf_url=pasted_link)     # the person pasted a link

`ingest_appliance` (below) is the one call behind POST /appliances: a plate photo or a typed model, then this.

The result is one of:
- CardReady: the card's path and the rail line;
- NeedPdf: the lookup missed (ws Notice kind `lookup_failed`); `maker_page` is the maker's own page for the model,
  which the app opens so the person can tap the manual and share it back;
- PlainPages: the ingest failed (ws Notice kind `ingest_failed`); the manual is kept and its pages still serve the
  whole-context prefix, with no card.

Curated cards live in the repo's cards/; cards built at run time for a household's appliance go to
FIXIT_PRIVATE/cards/homes/<household>/ (FIXIT_PRIVATE/cards/ when no household is given) and never into the repo.
Manuals go to FIXIT_PRIVATE/manuals/ the same way. A household's card reaches other homes only through
`share_card` (K5): a fetched manual whose own model list covers the model, or a reviewer's link, and never a second
version of a shared manual. A maker whose terms do not allow apps to read its manuals (Samsung) is Refused
(reason maker_terms) before any lookup, fetch, upload or call, with no switch.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import calls
from ingest import appliance_file, check_pr, lookup, models, pages
from ingest import plate as plate_mod
from ingest.cards import CARDS, all_cards, card_path, read_card, write_card
from ingest.progress import Tracker, Update

Covers = Literal["exact", "pattern"] | None
Origin = Literal["fetched", "uploaded"]


@dataclass(frozen=True)
class CardReady:
    path: Path
    card: dict[str, Any]
    rail: str
    built_now: bool
    rejected: tuple[appliance_file.Rejection, ...] = ()  # what the citation check dropped from a new build
    # K1: whether the manual's own model list covers this model; None with the reason (no_model_list: the manual
    # prints none; model_not_in_list: it prints others). Such a card stays private to the home that built it.
    covers: Covers = None
    covers_reason: str | None = None
    origin: Origin = "fetched"  # fetched from the maker's site, or the household's own upload (never shared)
    models: tuple[str, ...] = ()  # manual.models
    # F2, for a new build: each upkeep procedure the manual prints and what became of it (job, dropped, missing)
    coverage: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Refused:
    """A maker whose terms do not allow apps to read its manuals: no lookup, fetch, upload or card (IngestNotice
    kind `lookup_failed`, reason `maker_terms`)."""
    notice: str
    brand: str
    text: dict[str, str]
    code: str = "maker_terms"


@dataclass(frozen=True)
class NeedPdf:
    notice: str  # ws Notice kind
    reason: str
    maker_page: str | None
    text: dict[str, str]  # per language
    code: str = "no_manual"  # the contract's IngestNotice.reason


@dataclass(frozen=True)
class PlainPages:
    notice: str
    page_count: int
    rail: str
    error: str
    text: dict[str, str]
    code: str = "build_failed"


Outcome = CardReady | NeedPdf | PlainPages | Refused

# Makers whose terms forbid apps reading their manuals: refused in code, with no flag.
DECLINED_MAKERS = frozenset({"samsung"})
MAKER_TERMS = {"en": "This maker's terms don't allow apps to read its manuals, so I can't make a repair card "
                     "for it. You can still read the manual on the maker's own site.",
               "nl": "De voorwaarden van deze fabrikant staan niet toe dat apps zijn handleidingen lezen, dus ik "
                     "kan er geen reparatiekaart voor maken. Je kunt de handleiding wel lezen op de site van de "
                     "fabrikant."}

# A lookup miss as the contract's IngestNotice.reason, which the phone words.
NOTICE_REASON = {"robots": "site_blocked", "http_error": "site_blocked"}
MAX_PDF_BYTES = 52_428_800  # the upload's x-max-bytes in contracts 1.15

MAKER_NAMES = {"bosch": "Bosch", "siemens": "Siemens", "miele": "Miele", "aeg": "AEG"}


def private_cards() -> Path | None:
    """The shared run-time cards (built from a fetched manual and shared); a household's own are under homes/."""
    priv = calls.private_dir()
    return priv / "cards" if priv is not None else None


def home_cards(household: str) -> Path | None:
    root = private_cards()
    return pages.home_dir(root, household) if root is not None else None


def _card_files(root: Path, brand: str) -> list[Path]:
    """A brand's current cards (an archived version, in `<brand>/archive/`, is never found)."""
    return sorted((root / brand).glob("*.yaml")) if (root / brand).is_dir() else []


def household_of(path: Path) -> str | None:
    """The household whose own card this is (cards/homes/<household>/<brand>/<model>.yaml), or None."""
    return path.parent.parent.name if path.parent.parent.parent.name == "homes" else None


VARIANT_MAKERS = frozenset({"bosch", "siemens"})  # their E-Nr: the model, then "/" and a production number
_VARIANT = re.compile(r"\s*(.+?)\s*/\s*\d{1,3}\s*")


def variant_base(brand: str, model: str) -> str | None:
    """The model without a Bosch/Siemens production suffix, as its card is named ("SMV4HAX40N/39" is
    SMV4HAX40N), or None when there is no such suffix."""
    m = _VARIANT.fullmatch(model) if brand.lower() in VARIANT_MAKERS else None
    return m.group(1).replace(" ", "").upper() if m else None


def find_card(brand: str, model: str, household: str | None = None) -> Path | None:
    """The card for a model: by its file name or its aliases, ignoring case and spaces ("smv4hax40n" finds the
    SMV4HAX40N card), in the household's own cards first, then the repo's and the shared ones. Failing that, a
    shared card whose manual's own model list covers the model (K1: "WMN..." covers WMN16T3471); a card whose
    manual lists no models never links to another model. A Bosch or Siemens E-Nr's production suffix is not part
    of the model: "SMV4HAX40N/39" finds the SMV4HAX40N card (and one whose alias is "SMV4HAX40N/17") when no card
    is for "/39" itself."""
    shared = [CARDS] + ([p] if (p := private_cards()) else [])
    own = [h] if household is not None and (h := home_cards(household)) is not None else []
    wanted = [model] + ([base] if (base := variant_base(brand, model)) else [])
    for want_model in wanted:
        want = card_path(brand, want_model).name.lower()
        code = want_model.replace(" ", "").upper()
        for root in own + shared:
            files = _card_files(root, brand)
            for path in files:
                if path.name.lower() == want:
                    return path
            for other in files:
                aliases = read_card(other)["aliases"]
                if code in {a.replace(" ", "").upper() for a in aliases} | (
                        {variant_base(brand, a) for a in aliases} if base else set()):
                    return other
    for root in shared:
        for path in _card_files(root, brand):
            if models.covers(read_card(path)["manual"].get("models", []), model):
                return path
    return None


def card_ready(path: Path, card: dict[str, Any], rail: str, built_now: bool, model: str,
               rejected: tuple[appliance_file.Rejection, ...] = (),
               coverage: tuple[dict[str, Any], ...] = ()) -> CardReady:
    listed = card["manual"].get("models", [])
    cover = models.covers(listed, model)
    reason = None if cover else ("no_model_list" if not listed else "model_not_in_list")
    origin: Origin = "uploaded" if card["manual"].get("source") == "household_upload" else "fetched"
    return CardReady(path, card, rail, built_now, rejected, cover, reason, origin, tuple(listed), coverage)


def need_pdf(miss: lookup.Miss) -> NeedPdf:
    maker = MAKER_NAMES.get(miss.brand, miss.brand.title())
    if miss.maker_page:
        en = (f"I couldn't fetch the manual for {miss.model} myself. Open {maker}'s page, copy the link to the user "
              f"manual, and paste it under Manual link.")
        nl = (f"Ik kon de handleiding voor {miss.model} niet zelf ophalen. Open de pagina van {maker}, kopieer de link "
              f"naar de gebruikershandleiding en plak die hieronder.")
    else:
        en = (f"I couldn't find the manual for {miss.model}. Paste a link to its PDF from {maker}'s own site under "
              f"Manual link.")
        nl = (f"Ik vond de handleiding voor {miss.model} niet. Plak hieronder een link naar de pdf van de site van "
              f"{maker} zelf.")
    return NeedPdf("lookup_failed", miss.reason, miss.maker_page, {"en": en, "nl": nl},
                   NOTICE_REASON.get(miss.reason, "no_manual"))


TOO_LARGE = {"en": "That file is over 50 MB, too big for me to read. Paste a link to the manual under Manual link "
                   "instead.",
             "nl": "Dat bestand is groter dan 50 MB, te groot om te lezen. Plak hieronder liever een link naar de "
                   "handleiding."}
NOT_A_PDF = {"en": "That file isn't a PDF. Add the manual as a PDF, or paste a link to it under Manual link.",
             "nl": "Dat bestand is geen pdf. Voeg de handleiding toe als pdf, of plak hieronder een link ernaartoe."}


def bad_file(pdf: bytes) -> NeedPdf | None:
    """An uploaded or fetched file that can't be a manual: over the size cap, or not a PDF."""
    if len(pdf) > MAX_PDF_BYTES:
        return NeedPdf("ingest_failed", "too_large", None, TOO_LARGE, "too_large")
    if not pdf.startswith(b"%PDF"):
        return NeedPdf("ingest_failed", "not_a_pdf", None, NOT_A_PDF, "not_a_manual")
    return None


def plain_pages(page_count: int, rail: str, error: str, code: str = "build_failed") -> PlainPages:
    en = (f"I kept your manual ({page_count} pages) but couldn't build a checked card from it, so there are no "
          f"guided steps or reminders yet. You can still ask about it.")
    nl = (f"Ik heb je handleiding ({page_count} pagina's) bewaard, maar kon er geen gecontroleerde kaart van maken: "
          f"nog geen stappen of herinneringen. Je kunt er wel vragen over stellen.")
    return PlainPages("ingest_failed", page_count, rail, error, {"en": en, "nl": nl}, code)


async def add_appliance(brand: str, model: str, *, pdf: bytes | None = None, pdf_url: str | None = None,
                        lane: str = "l3", country: str = "nl", client: calls.Client | None = None,
                        fetch: lookup.Fetch | None = None,
                        on_progress: Callable[[str, str], None] | None = None, run_id: str | None = None,
                        track: Tracker | None = None, household_lang: str | None = None,
                        household: str | None = None, household_cap_usd: float | None = None,
                        build: bool = True) -> Outcome:
    """With `household`, a new card and its manual are that household's own (cards/homes/<household>/), and the
    household's cards are found first. With `build=False` (an add from Home Assistant) a model with no card gets its
    manual's pages and no card: nothing is built and no model is called, so nothing is spent."""
    def progress(stage: str, detail: str) -> None:
        if on_progress is not None:
            on_progress(stage, detail)

    if brand.lower() in DECLINED_MAKERS:
        return Refused("lookup_failed", brand.lower(), MAKER_TERMS)
    existing = find_card(brand, model, household)
    if existing is not None and pdf is None and pdf_url is None:
        card = read_card(existing)
        return card_ready(existing, card, appliance_file.rail_line(card["appliance_file_model"], card, []), False,
                          model)

    source_url = pdf_url or ""
    fetched = pdf is None and (pdf_url is None or lookup.maker_url_ok(pdf_url))
    if pdf is None:
        if pdf_url is None:
            progress("lookup", brand)
            # the maker's site, the PDF and its pages are read in a thread: the server's loop keeps answering /ws
            ref = await (asyncio.to_thread(lookup.lookup, brand, model, fetch=fetch) if fetch
                         else asyncio.to_thread(lookup.lookup, brand, model))
            if isinstance(ref, lookup.Miss):
                return need_pdf(ref)
            pdf_url = source_url = ref.url
        progress("fetch", pdf_url)
        try:
            pdf = await asyncio.to_thread(lookup.fetch_pdf, pdf_url)
        except Exception as e:  # any fetch failure asks for the file itself
            return need_pdf(lookup.Miss(brand, model, "http_error", str(e)[:200]))
    if (bad := bad_file(pdf)) is not None:
        return bad

    if fetched and not source_url.startswith("https://"):
        fetched = False  # a maker_site card's source_url is https (contracts 1.23)
    # an upload, or a link off the makers' sites, is the household's own file: cited by its digest, never a path
    source: appliance_file.Source = "maker_site" if fetched else "household_upload"
    if not fetched:
        source_url = f"urn:fixit:upload:{hashlib.sha256(pdf).hexdigest()}"
    path = pages.manual_path(brand, model, household)
    # built from its own copy (same file name: it is in the request), moved into place only as its outcome allows,
    # so two homes adding the same model at once never change each other's manual under them
    incoming = path.parent / ".incoming" / uuid.uuid4().hex / path.name
    incoming.parent.mkdir(parents=True)
    incoming.write_bytes(pdf)
    try:
        if not build:  # pages only: the card is built on request, by us
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                incoming.replace(path)
            try:
                count = len(await asyncio.to_thread(pages.load, path if not incoming.exists() else incoming))
            except Exception:
                count = 0
            return PlainPages("", count, "", "", {}, "")
        progress("ingest", f"{brand} {model}")
        res = await appliance_file.build(incoming, brand=brand, model=model, source_url=source_url, lane=lane,
                                         country=country, client=client, run_id=run_id, track=track,
                                         household_lang=household_lang, source=source, household=household,
                                         household_cap_usd=household_cap_usd)
        if res.card is None:
            if not path.exists():  # plain pages: the first manual for the model serves them; never replace one
                path.parent.mkdir(parents=True, exist_ok=True)
                incoming.replace(path)
            try:
                count = len(await asyncio.to_thread(pages.load, path if not incoming.exists() else incoming))
            except Exception:  # an unreadable PDF still says so
                count = 0
            return plain_pages(count, res.rail, res.error or "", "cap_reached" if res.cap_reached else "build_failed")
        path.parent.mkdir(parents=True, exist_ok=True)
        incoming.replace(path)  # the checked card cites these pages: the manual and its card go in together
        root = (home_cards(household) if household is not None else private_cards()) or CARDS
        written = write_card(res.card, root)
        # the same record `make check-pr` writes: without it the server treats a private card as unchecked
        errs, rec = await asyncio.to_thread(check_pr.check_local, written, path)
        if res.lifted:  # the heat zones the build lifted, for the "how this card was checked" panel
            rec["lifted_zones"] = res.lifted
        if res.checklist:  # each job a manual of this kind usually gives: built, a gap, or not in this manual
            rec["checklist"] = res.checklist
        if not errs:
            check_pr.record_path(written).write_text(json.dumps(rec, indent=1) + "\n")
        return card_ready(written, res.card, res.rail, True, model, tuple(res.rejected), tuple(res.coverage))
    finally:
        shutil.rmtree(incoming.parent, ignore_errors=True)


# ---- K5: a household's card reaches other homes only through these two calls ----------------------------------------

@dataclass(frozen=True)
class Shared:
    path: Path  # the shared card
    archived: Path | None = None  # the shared version a reviewed replacement moved aside


@dataclass(frozen=True)
class NotShared:
    reason: str  # the contract's CardRecord.private_reason
    detail: str = ""


def _shared_target(card: dict[str, Any]) -> Path:
    root = private_cards()
    if root is None:
        raise FileNotFoundError("FIXIT_PRIVATE is not set and the default private folder does not exist")
    return card_path(card["brand"], card["model"], root)


def _why_not_shared(card: dict[str, Any], reviewed_by: str | None) -> NotShared | None:
    manual = card["manual"]
    if card["brand"] in DECLINED_MAKERS:
        return NotShared("maker_terms")
    if manual.get("source") != "maker_site" or not manual.get("pdf_sha256"):
        return NotShared("uploaded", "a household's own upload is never shared")
    if reviewed_by is None and not models.covers(manual.get("models", []), card["model"]):
        return (NotShared("no_model_list", "the manual prints no model codes") if not manual.get("models") else
                NotShared("model_not_in_list", "the manual's model list does not cover this model"))
    return None


def _codes_unconfirmed(path: Path) -> NotShared | None:
    """Fault codes read from a drawing, and our own restore steps, are shared only once a person has confirmed or
    accepted each (tools/confirm_codes)."""
    rec = json.loads(rf.read_text()) if (rf := check_pr.record_path(path)).exists() else {}
    if rec.get("card_sha256") != check_pr.card_sha256(path):
        return NotShared("version_conflict", "no check record for this card version")
    if left := check_pr.unconfirmed(rec):
        return NotShared("codes_unconfirmed", f"drawn codes not yet confirmed by a person: {', '.join(left)}")
    if left := check_pr.unaccepted(rec):
        return NotShared("steps_unaccepted", f"our own steps not yet accepted by a person: {', '.join(left)}")
    return None


def _share(path: Path, card: dict[str, Any], target: Path) -> None:
    """The card, its check record and its manual, copied to the shared places (the manual first: the card cites it)."""
    manual_from = pages.manual_path(card["brand"], card["model"], household_of(path))
    manual_to = pages.manual_path(card["brand"], card["model"])
    if manual_from.exists() and manual_from != manual_to:
        manual_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manual_from, manual_to)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    if (rec := check_pr.record_path(path)).exists():
        shutil.copyfile(rec, check_pr.record_path(target))


def share_card(path: Path, reviewed_by: str | None = None) -> Shared | NotShared:
    """Share a household's card with every home (K5). Only a card built from the maker's own manual, fetched (never
    an upload), whose own model list covers the model; or, with `reviewed_by`, one a person linked through the K4
    review queue (the caller records who and when). A shared card with a different manual is a version conflict:
    replacing it is `replace_shared`, a separate, explicit call. Sharing the same version again is a no-op."""
    card = read_card(path)
    if (why := _why_not_shared(card, reviewed_by) or _codes_unconfirmed(path)) is not None:
        return why
    target = _shared_target(card)
    if target.exists():
        theirs = read_card(target)["manual"].get("pdf_sha256", "")
        if theirs != card["manual"]["pdf_sha256"]:
            return NotShared("version_conflict", f"shared card is {theirs[:12] or 'unversioned'}")
        return Shared(target)
    _share(path, card, target)
    return Shared(target)


def replace_shared(path: Path, reviewed_by: str) -> Shared | NotShared:
    """A reviewed replacement of the shared card with this household's version: the old one is archived as
    `<brand>/archive/<model>.<sha12>.yaml` (never found by `find_card`), then this one is shared as `share_card`
    would. A new card is a different card file, not only a different manual: a reviewed rebuild of the same PDF
    replaces the shared card too (archived by the old card's own digest, the manual left in place), and only the same
    card file is a no-op."""
    card = read_card(path)
    if not reviewed_by.strip():
        return NotShared("version_conflict", "a replacement needs a reviewer")
    if (why := _why_not_shared(card, reviewed_by) or _codes_unconfirmed(path)) is not None:
        return why
    target = _shared_target(card)
    archived = None
    if target.exists():
        if check_pr.card_sha256(target) == check_pr.card_sha256(path):
            return Shared(target)
        theirs = read_card(target)["manual"].get("pdf_sha256", "")
        same_manual = theirs == card["manual"]["pdf_sha256"]
        key = check_pr.card_sha256(target) if same_manual or not theirs else theirs
        archived = target.parent / "archive" / f"{target.stem}.{key[:12]}.yaml"
        archived.parent.mkdir(exist_ok=True)
        target.replace(archived)
        if (rec := check_pr.record_path(target)).exists():
            rec.replace(check_pr.record_path(archived))
        old_pdf = pages.manual_path(card["brand"], card["model"])
        if not same_manual and old_pdf.exists():  # the pages the archived card cites
            (old_pdf.parent / "archive").mkdir(exist_ok=True)
            old_pdf.replace(old_pdf.parent / "archive" / f"{old_pdf.stem}.{key[:12]}.pdf")
    _share(path, card, target)
    return Shared(target, archived)


# ---- POST /appliances: one call from a plate photo or a typed model to a card --------------------------------------

Stage = Literal["plate", "lookup", "fetch", "ingest", "done"]  # the contract's Ingest.stage
Status = Literal["checked", "pages_only", "failed", "pending"]  # the contract's Appliance.card_status
Progress = Callable[[str, str], None]


CLOSER_PHOTO = {"en": "I couldn't read the plate. Take a closer photo, straight on, in good light.",
                "nl": "Ik kon het typeplaatje niet lezen. Maak een foto van dichterbij, recht ervoor, met goed licht."}


NO_BRAND = {"en": "I don't know which maker that model code is from. Take a photo of the plate, or paste a link to the "
                  "manual's PDF under Manual link.",
            "nl": "Ik weet niet van welk merk die modelcode is. Maak een foto van het typeplaatje, of plak hieronder "
                  "een link naar de pdf van de handleiding."}


@dataclass(frozen=True)
class ApplianceIngest:
    status: Status
    stage: Stage
    brand: str | None
    model: str | None
    plate_confirmed: bool
    card_path: Path | None = None
    card: dict[str, Any] | None = None
    rail: str = ""
    notice: dict[str, Any] | None = None  # the contract's IngestNotice: {kind, text: {en, nl}, maker_page}
    plate: plate_mod.PlateCard | None = None
    rejected: tuple[appliance_file.Rejection, ...] = ()
    # why a build failed, for the builder's own runs: it can quote card text, so log it only under FIXIT_PRIVATE
    error: str = ""
    covers: Covers = None  # K1, as on CardReady
    covers_reason: str | None = None
    origin: Origin | None = None
    models: tuple[str, ...] = ()
    coverage: tuple[dict[str, Any], ...] = ()  # as on CardReady


def known_brands() -> dict[str, str]:
    """Every card's model code and aliases (compact) → its brand."""
    roots = [CARDS] + ([p] if (p := private_cards()) else [])
    out: dict[str, str] = {}
    for root in roots:
        for f in all_cards(root):
            card = read_card(f)
            for code in (card["model"], *card["aliases"]):
                out.setdefault(plate_mod.compact(code), card["brand"])
    return out


def known_models() -> list[str]:
    return list(known_brands())


def brand_for(model: str) -> str | None:
    """The brand of a typed model with none given: a known card or alias first, then the makers' code shapes."""
    code = plate_mod.compact(model)
    return known_brands().get(code) or known_brands().get(code.split("/")[0]) or plate_mod.guess_brand(code)


async def ingest_appliance(*, kind: str | None = None, brand: str | None = None, model: str | None = None,
                           plate_jpeg: bytes | None = None, pdf: bytes | None = None, pdf_url: str | None = None,
                           country: str = "nl", lane: str = "l3", household: str | None = None,
                           on_progress: Progress | None = None, client: calls.Client | None = None,
                           fetch: lookup.Fetch | None = None, on_update: Update | None = None,
                           household_lang: str | None = None,
                           household_cap_usd: float | None = None, build: bool = True) -> ApplianceIngest:
    """Plate photo or typed model → lookup → ingest → card, for POST /appliances (run in the background).
    A typed brand and model win over the plate; a model with no brand gets one from the known cards, then from the
    makers' code shapes (`brand_for`). `kind` is optional: the card carries its own. A plate that needs a
    tap is pending at stage plate with `plate` set; one that cannot be read has the notice plate_unreadable.
    Every call is tagged in the ledger as add-appliance:<brand>/<model>:<id> ("unknown" for what the plate has not
    told yet), and the build inside it as card-build:<brand>/<model>:<id>, with the same id.
    `on_progress` gets the stage word and a detail string; `on_update` gets the contract's Ingest progress fields
    (ingest.progress: progress, elapsed_s, eta_s, manual, quotes, jobs_ready) after every change. Its job titles
    and the manual's title are untrusted text for the server's G0 check. Every call is `household`'s, under its
    daily cap `household_cap_usd`."""
    run_id = appliance_file.new_run_id()
    with calls.run_tag(f"add-appliance:{(brand or 'unknown').lower()}/{model or 'unknown'}:{run_id}"):
        return await _ingest_appliance(kind=kind, brand=brand, model=model, plate_jpeg=plate_jpeg, pdf=pdf,
                                       pdf_url=pdf_url, country=country, lane=lane, household=household,
                                       on_progress=on_progress, client=client, fetch=fetch, run_id=run_id,
                                       track=Tracker(on_update), household_lang=household_lang,
                                       household_cap_usd=household_cap_usd, build=build)


async def _ingest_appliance(*, kind: str | None, brand: str | None, model: str | None, plate_jpeg: bytes | None,
                            pdf: bytes | None, pdf_url: str | None, country: str, lane: str, household: str | None,
                            on_progress: Progress | None, client: calls.Client | None, fetch: lookup.Fetch | None,
                            run_id: str, track: Tracker, household_lang: str | None,
                            household_cap_usd: float | None, build: bool = True) -> ApplianceIngest:
    def progress(stage: str, detail: str = "") -> None:
        if on_progress is not None:
            on_progress(stage, detail)

    if brand and brand.lower() in DECLINED_MAKERS:  # before the plate read, the lookup or any call
        return _declined(brand, model, True)
    if model and not brand:
        brand = brand_for(model)
        if brand is None and plate_jpeg is None:
            return ApplianceIngest("failed", "lookup", None, model, True, notice=_notice("lookup_failed", NO_BRAND))
    confirmed = bool(brand and model)
    read: plate_mod.PlateCard | None = None
    if not (brand and model):
        if plate_jpeg is None:
            return ApplianceIngest("failed", "plate", brand, model, False, notice=_notice("plate_unreadable", {
                "en": "Type the model code from the plate, or take a photo of the plate.",
                "nl": "Typ de modelcode van het typeplaatje, of maak er een foto van."}))
        progress("plate")
        read = await plate_mod.read_plate(plate_jpeg, lane=lane, household=household, known_models=known_models(),
                                          client=client, household_cap_usd=household_cap_usd)
        brand = brand or read.brand
        if read.closer_photo or not brand or not read.model:
            return ApplianceIngest("pending", "plate", brand, None, False, plate=read, notice=_notice(
                "plate_unreadable", CLOSER_PHOTO))
        if read.confirm_needed:
            return ApplianceIngest("pending", "plate", brand, read.model, False, plate=read)
        model = read.model
    assert brand and model
    brand = plate_mod.brand_slug(brand) or brand.lower()
    if brand in DECLINED_MAKERS:
        return _declined(brand, model, confirmed or read is not None, read)
    if pdf_url is not None and not lookup.maker_url_ok(pdf_url):
        return ApplianceIngest("failed", "fetch", brand, model, confirmed or read is not None, notice=_notice(
            "lookup_failed", need_pdf(lookup.Miss(brand, model, "http_error")).text, reason="no_manual"))

    out = await add_appliance(brand, model, pdf=pdf, pdf_url=pdf_url, lane=lane, country=country, client=client,
                              fetch=fetch, on_progress=progress, run_id=run_id, track=track,
                              household_lang=household_lang, household=household,
                              household_cap_usd=household_cap_usd, build=build)
    progress("done", type(out).__name__)
    plate_ok = confirmed or read is not None
    if isinstance(out, CardReady):
        return ApplianceIngest("checked", "done", brand, model, plate_ok, out.path, out.card, out.rail, plate=read,
                               rejected=out.rejected, covers=out.covers, covers_reason=out.covers_reason,
                               origin=out.origin, models=out.models, coverage=out.coverage)
    if isinstance(out, NeedPdf):
        return ApplianceIngest("failed", "done", brand, model, plate_ok, notice=_notice(
            out.notice, out.text, out.maker_page, out.code), plate=read)
    if isinstance(out, Refused):
        return _declined(brand, model, plate_ok, read)
    return ApplianceIngest("pages_only", "done", brand, model, plate_ok, rail=out.rail,
                           notice=_notice(out.notice, out.text, reason=out.code) if out.notice else None, plate=read,
                           error=out.error)


def _declined(brand: str, model: str | None, plate_ok: bool,
              plate: plate_mod.PlateCard | None = None) -> ApplianceIngest:
    return ApplianceIngest("failed", "lookup", brand.lower(), model, plate_ok, plate=plate,
                           notice=_notice("lookup_failed", MAKER_TERMS, reason="maker_terms"))


def _notice(kind: str, text: dict[str, str], maker_page: str | None = None,
            reason: str | None = None) -> dict[str, Any]:
    """The contract's IngestNotice: fixed, code-written text, never model text, and its reason code when known."""
    out = {"kind": kind, "text": text, "maker_page": maker_page}
    if reason is not None:
        out["reason"] = reason
    return out
