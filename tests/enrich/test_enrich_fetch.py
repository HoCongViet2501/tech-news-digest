"""enrich() / enrich_many() over respx: politeness, limits and failure handling."""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest
import respx

from conftest import fixture_bytes
from digest.enrich import Enrichment, RobotsCache, enrich, enrich_many
from digest.enrich import core as enrich_core
from digest.http import make_client

HTML = {"content-type": "text/html; charset=utf-8"}
PAGE = "https://blog.example.com/posts/autovacuum"


def run[T](coro_fn: Callable[[httpx.AsyncClient], Awaitable[T]]) -> T:
    async def go() -> T:
        async with make_client() as client:
            return await coro_fn(client)

    return asyncio.run(go())


def enrich_one(url: str = PAGE, timeout_s: float = 5) -> Enrichment:
    robots = RobotsCache()
    return run(lambda client: enrich(client, url, timeout_s=timeout_s, robots=robots))


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    # A per-test router: routes added in fixtures must not leak into the global one.
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def no_robots(router: respx.MockRouter) -> respx.Route:
    return router.get(url__regex=r"https://[^/]+/robots\.txt").respond(404)


def test_enriches_html_page(router: respx.MockRouter, no_robots: respx.Route) -> None:
    route = router.get(PAGE).respond(
        200, headers=HTML, content=fixture_bytes("enrich/og_tags.html")
    )

    e = enrich_one()

    assert e.image_url == "https://cdn.example.com/covers/autovacuum.png"
    assert e.description == "How & why autovacuum falls behind."
    assert e.reading_min == 1
    assert e.lang == "en"
    assert e.skip_reason is None
    assert e.main_text
    assert "text/html" in route.calls.last.request.headers["Accept"]


def test_relative_image_resolves_against_final_url_after_redirect(
    router: respx.MockRouter, no_robots: respx.Route
) -> None:
    html = b'<html><head><meta property="og:image" content="../img/a.png"></head></html>'
    router.get(PAGE).respond(301, headers={"location": "https://blog.example.com/new/post"})
    router.get("https://blog.example.com/new/post").respond(200, headers=HTML, content=html)

    assert enrich_one().image_url == "https://blog.example.com/img/a.png"


def test_vietnamese_and_english_pages(router: respx.MockRouter, no_robots: respx.Route) -> None:
    vi_url, en_url = "https://viblo.asia/p/django", "https://dev.to/ben/long-live-rails"
    router.get(vi_url).respond(
        200, headers=HTML, content=fixture_bytes("enrich/real_viblo_vi.html")
    )
    router.get(en_url).respond(
        200, headers=HTML, content=fixture_bytes("enrich/real_devto_en.html")
    )

    results = run(lambda client: enrich_many(client, [vi_url, en_url], max_items=10))

    assert results[vi_url].lang == "vi"
    assert results[en_url].lang == "en"
    assert all(r.image_url and r.description and r.reading_min for r in results.values())


def test_broken_charset_header(router: respx.MockRouter, no_robots: respx.Route) -> None:
    headers = {"content-type": "text/html; charset=no-such-charset"}
    router.get(PAGE).respond(
        200, headers=headers, content=fixture_bytes("enrich/broken_charset.html")
    )

    e = enrich_one()

    assert e.skip_reason is None
    assert e.description and e.description.startswith("Caf")


@pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/json", None])
def test_non_html_is_skipped(
    router: respx.MockRouter, no_robots: respx.Route, ctype: str | None
) -> None:
    headers = {"content-type": ctype} if ctype else {}
    router.get(PAGE).respond(200, headers=headers, content=b"%PDF-1.7 ...")

    e = enrich_one()

    assert e == Enrichment(skip_reason=f"content-type {ctype or 'missing'}")


def test_http_error_is_skipped(router: respx.MockRouter, no_robots: respx.Route) -> None:
    router.get(PAGE).respond(404, headers=HTML, content=b"<html>gone</html>")

    assert enrich_one() == Enrichment(skip_reason="http 404")


def test_oversize_body_stops_reading_and_keeps_head(
    router: respx.MockRouter, no_robots: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enrich_core, "MAX_BODY_BYTES", 64 * 1024)
    sent: list[int] = []
    head = b'<html><head><meta property="og:image" content="https://cdn.e.com/i.png"></head><body>'

    async def endless() -> AsyncIterator[bytes]:
        yield head
        for _ in range(10_000):  # ~160 MB if fully read
            sent.append(1)
            yield b"<p>" + b"filler words here " * 900 + b"</p>"

    router.get(PAGE).mock(return_value=httpx.Response(200, headers=HTML, content=endless()))

    e = enrich_one()

    assert e.image_url == "https://cdn.e.com/i.png"
    assert e.skip_reason is None
    assert len(sent) <= 5


def test_transport_timeout(router: respx.MockRouter, no_robots: respx.Route) -> None:
    router.get(PAGE).mock(side_effect=httpx.ReadTimeout("slow"))

    assert enrich_one() == Enrichment(skip_reason="ReadTimeout")


def test_overall_timeout_on_slow_trickle(router: respx.MockRouter, no_robots: respx.Route) -> None:
    async def trickle() -> AsyncIterator[bytes]:
        while True:  # each chunk arrives in time, but the page never ends
            await asyncio.sleep(0.02)
            yield b"<p>x</p>"

    router.get(PAGE).mock(return_value=httpx.Response(200, headers=HTML, content=trickle()))

    assert enrich_one(timeout_s=0.2) == Enrichment(skip_reason="timeout")


def test_robots_disallow_skips_fetch(router: respx.MockRouter) -> None:
    router.get("https://blog.example.com/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /posts/"
    )
    page = router.get(PAGE).respond(200, headers=HTML, content=b"<html></html>")

    e = enrich_one()

    assert e == Enrichment(skip_reason="robots: disallowed")
    assert not page.called


def test_robots_404_allows_fetch(router: respx.MockRouter) -> None:
    router.get("https://blog.example.com/robots.txt").respond(404)
    page = router.get(PAGE).respond(200, headers=HTML, content=fixture_bytes("enrich/no_meta.html"))

    e = enrich_one()

    assert page.called
    assert e.description and e.reading_min == 1


def test_robots_500_skips_host(router: respx.MockRouter) -> None:
    router.get("https://blog.example.com/robots.txt").respond(500)
    page = router.get(PAGE).respond(200, headers=HTML, content=b"<html></html>")

    assert enrich_one() == Enrichment(skip_reason="robots: http 500")
    assert not page.called


def test_unexpected_error_never_raises(
    router: respx.MockRouter, no_robots: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    router.get(PAGE).respond(200, headers=HTML, content=b"<html></html>")

    def boom(html: str, url: str) -> Enrichment:
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(enrich_core, "parse_page", boom)

    assert enrich_one() == Enrichment(skip_reason="RuntimeError")


# --- enrich_many -------------------------------------------------------------------------


def _counting_side_effect(in_flight: defaultdict[str, int], peak: defaultdict[str, int]) -> Any:
    async def side_effect(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        in_flight[host] += 1
        in_flight["*"] += 1
        peak[host] = max(peak[host], in_flight[host])
        peak["*"] = max(peak["*"], in_flight["*"])
        await asyncio.sleep(0.01)
        in_flight[host] -= 1
        in_flight["*"] -= 1
        return httpx.Response(200, headers=HTML, content=b"<html><body><p>hi</p></body></html>")

    return side_effect


def test_concurrency_limits(router: respx.MockRouter, no_robots: respx.Route) -> None:
    in_flight: defaultdict[str, int] = defaultdict(int)
    peak: defaultdict[str, int] = defaultdict(int)
    router.get(url__regex=r"https://h\d\.example/p/\d+").mock(
        side_effect=_counting_side_effect(in_flight, peak)
    )
    urls = [f"https://h{h}.example/p/{i}" for h in range(4) for i in range(6)]

    results = run(
        lambda client: enrich_many(client, urls, concurrency=5, per_host=2, max_items=100)
    )

    assert len(results) == 24
    assert all(r.skip_reason is None for r in results.values())
    assert max(v for k, v in peak.items() if k != "*") == 2
    assert peak["*"] == 5


def test_max_items_and_duplicates(router: respx.MockRouter, no_robots: respx.Route) -> None:
    page = router.get(url__regex=r"https://e\.com/\d").respond(200, headers=HTML, content=b"<p>")
    urls = ["https://e.com/1", "https://e.com/1", "https://e.com/2", "https://e.com/3"]

    results = run(lambda client: enrich_many(client, urls, max_items=2))

    assert list(results) == ["https://e.com/1", "https://e.com/2"]
    assert page.call_count == 2


def test_robots_fetched_once_per_host_across_many(router: respx.MockRouter) -> None:
    robots = router.get("https://e.com/robots.txt").respond(200, text="User-agent: *\nAllow: /")
    router.get(url__regex=r"https://e\.com/\d").respond(200, headers=HTML, content=b"<p>")
    urls = [f"https://e.com/{i}" for i in range(6)]

    run(lambda client: enrich_many(client, urls, max_items=10))

    assert robots.call_count == 1
