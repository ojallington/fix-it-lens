"""The manual lookup on synthetic pages (never a maker's real HTML): robots.txt decides before any request, a miss
carries the maker's page for the one-tap path, and Samsung is never looked up."""

from __future__ import annotations

import httpx
import pytest

from ingest import lookup as lk

BOSCH_ROBOTS = "User-agent: *\nDisallow: /*ajax/\nDisallow: /manual/\nDisallow: /*supportdetail/\n"
class FakeWeb:
    def __init__(self, pages: dict[str, tuple[int, str]]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def __call__(self, url: str) -> tuple[int, str]:
        self.requested.append(url)
        return self.pages.get(url, (404, ""))


@pytest.fixture(autouse=True)
def _fresh_robots() -> None:
    lk._robots.clear()


def test_bosch_support_pages_are_disallowed_so_it_is_a_miss_with_the_maker_page() -> None:
    web = FakeWeb({"https://www.bosch-home.nl/robots.txt": (200, BOSCH_ROBOTS)})
    r = lk.lookup("bosch", "SMV4HAX40N/17", fetch=web)
    assert isinstance(r, lk.Miss) and r.reason == "robots"
    assert r.maker_page == "https://www.bosch-home.nl/supportdetail/product/SMV4HAX40N/17"
    assert web.requested == ["https://www.bosch-home.nl/robots.txt"]  # the support page itself is never fetched


def test_samsung_is_never_looked_up() -> None:
    web = FakeWeb({})
    r = lk.lookup("samsung", "WW00X", fetch=web)  # its terms forbid apps reading its manuals (K1): no adapter
    assert isinstance(r, lk.Miss) and r.reason == "not_covered" and web.requested == []
    assert not lk.maker_url_ok("https://org.downloadcenter.samsung.com/x.pdf")


def test_unknown_model_is_not_found() -> None:
    web = FakeWeb({"https://www.bosch-home.nl/robots.txt": (200, "User-agent: *\nDisallow:\n")})
    r = lk.lookup("bosch", "NOPE123", fetch=web)
    assert isinstance(r, lk.Miss) and r.reason == "not_found"


def test_maker_not_covered_makes_no_request() -> None:
    web = FakeWeb({})
    r = lk.lookup("acme", "WM-0000", fetch=web)
    assert isinstance(r, lk.Miss) and r.reason == "not_covered" and web.requested == []


def test_no_robots_file_allows() -> None:
    assert lk.robots_allows("https://media3.example.invalid/Documents/1.pdf", FakeWeb({}))


def test_star_disallow_is_honoured() -> None:
    web = FakeWeb({"https://www.bosch-home.nl/robots.txt": (200, BOSCH_ROBOTS)})
    assert not lk.robots_allows("https://www.bosch-home.nl/nl/supportdetail/product/X/1", web)
    assert lk.robots_allows("https://www.bosch-home.nl/nl/product-list", web)


def test_bsh_parser_takes_the_user_manual() -> None:
    body = ('{"type":"installation-instruction",'
            '"url":"https:\\/\\/media3.bsh-group.com\\/Documents\\/9001594147_C.pdf"},'
            '{"type":"user-manuals","url":"https:\\/\\/media3.bsh-group.com\\/Documents\\/9001522704_C.pdf"}')
    r = lk._parse_bsh("bosch", "SMV4HAX40N/17", "https://x", body)
    assert isinstance(r, lk.ManualRef) and r.doc_id == "9001522704"


@pytest.mark.parametrize("url, ok", [
    ("https://media3.bsh-group.com/Documents/9001522704_C.pdf", True),
    ("https://www.bosch-home.nl/x.pdf", True),
    ("http://media3.bsh-group.com/x.pdf", False),
    ("https://www.manualslib.com/x.pdf", False),
    ("https://bsh-group.com.evil.example/x.pdf", False),
    ("https://u:p@www.bosch-home.nl/x.pdf", False),
    ("https://www.bosch-home.nl:8443/x.pdf", False),
    ("https://www.bosch-home.nl:443/x.pdf", True),
])
def test_maker_url_ok(url: str, ok: bool) -> None:
    assert lk.maker_url_ok(url) is ok


@pytest.mark.parametrize("to", ["https://evil.example.com/m.pdf", "http://media3.bosch-home.com/m.pdf",
                                "https://user:pw@media3.bosch-home.com/m.pdf"])
def test_a_maker_link_that_redirects_off_the_makers_sites_is_never_fetched(monkeypatch: pytest.MonkeyPatch,
                                                                            to: str) -> None:
    got: list[str] = []

    def get(url: str, **kw: object) -> httpx.Response:
        assert kw["follow_redirects"] is False
        got.append(url)
        return httpx.Response(302, headers={"location": to}, request=httpx.Request("GET", url))

    monkeypatch.setattr(lk, "robots_allows", lambda url: True)
    monkeypatch.setattr(lk.httpx, "get", get)
    with pytest.raises(lk.FetchRefused):
        lk.fetch_pdf("https://media3.bosch-home.com/Documents/1.pdf")
    assert got == ["https://media3.bosch-home.com/Documents/1.pdf"]  # the redirect's target is never asked for


def test_a_redirect_on_the_makers_site_is_followed_a_few_times_only(monkeypatch: pytest.MonkeyPatch) -> None:
    hops = iter(["/a.pdf", "/b.pdf"])

    def get(url: str, **kw: object) -> httpx.Response:
        req = httpx.Request("GET", url)
        if (nxt := next(hops, None)) is not None:
            return httpx.Response(301, headers={"location": nxt}, request=req)
        return httpx.Response(200, content=b"%PDF-1.4 a manual", request=req)

    monkeypatch.setattr(lk, "robots_allows", lambda url: True)
    monkeypatch.setattr(lk.httpx, "get", get)
    assert lk.fetch_pdf("https://media3.bosch-home.com/Documents/1.pdf") == b"%PDF-1.4 a manual"
    monkeypatch.setattr(lk.httpx, "get", lambda url, **kw: httpx.Response(
        302, headers={"location": "/again.pdf"}, request=httpx.Request("GET", url)))
    with pytest.raises(lk.FetchRefused, match="too many"):
        lk.fetch_pdf("https://media3.bosch-home.com/Documents/1.pdf")
