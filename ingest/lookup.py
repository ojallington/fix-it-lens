"""The manual lookup: one request to the maker's own support site per lookup, never a mirror, never ManualsLib.

    ref = lookup("bosch", "SMV4HAX40N")         # ManualRef(url=..., lang="nl", ...) or Miss(reason=...)

Each maker in contracts/lookup_makers.json has an adapter: the support page for a model code, and how to find the
user manual on it. A miss says why, and the app asks for the PDF (a link to the maker's page, or an upload).

robots.txt is checked before the request (cached per host per process). A path the site disallows is a miss with
reason "robots"; nothing is fetched, user-initiated or not. The miss then makes the person the
browser: `maker_page` is the maker's own support page for the model, which the app opens on the phone ("Open Bosch's
page for SMV4HAX40N"); the person taps the manual, and the share sheet or a pasted link hands it to the app.
`fetch_pdf` fetches a link the person gives us, under the same robots rule; a host that disallows it needs the file
itself. Nothing is cached or mirrored: the PDF goes to this household's folder in FIXIT_PRIVATE.
"""

from __future__ import annotations

import html
import re
import time
import urllib.parse
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx

USER_AGENT = "FixItLens/0.1 (user-initiated manual lookup; one request, no mirroring)"
TIMEOUT_S = 15.0
ROBOTS_TTL_S = 24 * 3600

MissReason = Literal["not_covered", "robots", "not_found", "no_manual", "http_error"]


@dataclass(frozen=True)
class ManualRef:
    brand: str
    model: str
    url: str  # the PDF, on the maker's own host
    lang: str
    source_page: str  # the support page it was listed on
    doc_id: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class Miss:
    brand: str
    model: str
    reason: MissReason
    detail: str = ""
    maker_page: str | None = None  # open this on the phone for the one-tap path


Fetch = Callable[[str], tuple[int, str]]  # url -> (status, body)


def _http_fetch(url: str) -> tuple[int, str]:
    r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True)
    return r.status_code, r.text


_robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser]] = {}


def robots_allows(url: str, fetch: Fetch = _http_fetch) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc}"
    cached = _robots.get(host)
    if cached is None or time.time() - cached[0] > ROBOTS_TTL_S:
        rp = urllib.robotparser.RobotFileParser()
        try:
            status, body = fetch(host + "/robots.txt")
        except httpx.HTTPError:
            status, body = 0, ""
        if status == 200:
            rp.parse(body.splitlines())
        elif status in (401, 403):
            rp.parse(["User-agent: *", "Disallow: /"])
        else:
            rp.parse([])  # no robots.txt: nothing disallowed
        cached = (time.time(), rp)
        _robots[host] = cached
    return cached[1].can_fetch(USER_AGENT, url) and _star_rules_allow(cached[1], url)


def _star_rules_allow(rp: urllib.robotparser.RobotFileParser, url: str) -> bool:
    """urllib's parser ignores `*` wildcards inside paths (`Disallow: /*supportdetail/`); apply those ourselves."""
    entry = getattr(rp, "default_entry", None)
    if entry is None:
        return True
    path = urllib.parse.urlsplit(url).path or "/"
    for line in entry.rulelines:
        rule = urllib.parse.unquote(line.path)  # urllib stores the path quoted: "*" as "%2A"
        if "*" not in rule:
            continue
        pattern = "^" + ".*".join(re.escape(p) for p in rule.split("*"))
        if re.search(pattern, path):
            return bool(line.allowance)
    return True


# ---- makers ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Maker:
    support_url: Callable[[str], str]
    parse: Callable[[str, str, str, str], ManualRef | Miss]  # (brand, model, page_url, body)


def _bsh_url(site: str) -> Callable[[str], str]:
    def url(model: str) -> str:
        enr, _, variant = model.partition("/")
        tail = f"/{variant}" if variant else ""
        return f"https://www.{site}/supportdetail/product/{urllib.parse.quote(enr)}{tail}"
    return url


_BSH_DOC = re.compile(r"https://media3\.bsh-group\.com/Documents/(\d{10})_([A-Z])\.pdf")
_BSH_USER_MANUAL = re.compile(r"user-manual|gebruikershandleiding|gebrauchsanleitung", re.IGNORECASE)


def _parse_bsh(brand: str, model: str, page: str, body: str) -> ManualRef | Miss:
    """The support page embeds its document list as JSON: take the first PDF whose entry names a user manual.
    (The shape is from a saved page; the live page is behind a robots disallow, so it is unverified.)"""
    text = html.unescape(body).replace("\\/", "/")
    for m in _BSH_DOC.finditer(text):
        context = text[max(0, m.start() - 400): m.start()]
        if _BSH_USER_MANUAL.search(context):
            return ManualRef(brand, model, m.group(0), "nl", page, doc_id=m.group(1))
    return Miss(brand, model, "no_manual", "no user manual listed on the support page")


MAKERS: dict[str, Maker] = {
    "bosch": Maker(_bsh_url("bosch-home.nl"), _parse_bsh),
    "siemens": Maker(_bsh_url("siemens-home.bsh-group.com/nl"), _parse_bsh),
}


def lookup(brand: str, model: str, *, fetch: Fetch = _http_fetch) -> ManualRef | Miss:
    """Find this model's user manual on its maker's own support site: robots.txt, then one request."""
    maker = MAKERS.get(brand)
    if maker is None:
        return Miss(brand, model, "not_covered", f"no lookup for {brand} yet")
    page = maker.support_url(model.strip())
    if not robots_allows(page, fetch):
        return Miss(brand, model, "robots", f"{urllib.parse.urlsplit(page).netloc} disallows its support pages", page)
    try:
        status, body = fetch(page)
    except httpx.HTTPError as e:
        return Miss(brand, model, "http_error", type(e).__name__, page)
    if status == 404:
        return Miss(brand, model, "not_found", "the maker's site has no page for this model code")
    if status != 200:
        return Miss(brand, model, "http_error", f"HTTP {status}", page)
    found = maker.parse(brand, model, page, body)
    if isinstance(found, Miss):
        return Miss(found.brand, found.model, found.reason, found.detail, page)
    return found


# The makers' own domains (a host matches itself or any subdomain). A pasted manual link is fetched only from these.
MAKER_DOMAINS: dict[str, tuple[str, ...]] = {
    "bosch": ("bosch-home.nl", "bosch-home.com", "bsh-group.com"),
    "siemens": ("siemens-home.bsh-group.com", "bsh-group.com"),
    "miele": ("miele.nl", "miele.com", "miele.de"),
    "aeg": ("aeg.nl", "aeg.com", "electrolux.com", "electroluxmedia.com"),
}


def maker_url_ok(url: str) -> bool:
    """https on one of the lookup makers' own domains, no credentials, port 443 (contracts 1.6.0: anything else is a
    422, never a fetch)."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        return False
    if parts.username is not None or parts.password is not None or port not in (None, 443):
        return False
    return parts.scheme == "https" and any(host == d or host.endswith("." + d)
                                           for ds in MAKER_DOMAINS.values() for d in ds)


MAX_REDIRECTS = 5


class FetchRefused(Exception):
    """The host's robots.txt disallows this URL: ask for the file itself (share sheet or upload)."""


MAX_PDF_BYTES = 50 * 1024 * 1024


def fetch_pdf(url: str) -> bytes:
    """One GET of a manual link the person gave us. Refused where robots.txt disallows it; never cached here. A
    redirect is followed only to another https link on a maker's own site that its robots.txt allows, at most
    MAX_REDIRECTS times."""
    for _ in range(MAX_REDIRECTS + 1):
        if not maker_url_ok(url):
            raise FetchRefused("only https links on a maker's own site")
        if not robots_allows(url):
            raise FetchRefused(f"{urllib.parse.urlsplit(url).netloc} disallows fetching this link; "
                               "share the file instead")
        r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=60.0, follow_redirects=False)
        if not r.is_redirect:
            break
        url = urllib.parse.urljoin(url, r.headers["location"])
    else:
        raise FetchRefused("the link redirects too many times")
    r.raise_for_status()
    if len(r.content) > MAX_PDF_BYTES or not r.content.startswith(b"%PDF"):
        raise FetchRefused("the link is not a PDF of at most 50 MB")
    return r.content
