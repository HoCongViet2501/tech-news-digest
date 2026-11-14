import httpx
import pytest

from digest.offline import FIXTURES_DIR, ROUTES, offline_transport


def test_every_route_has_a_fixture() -> None:
    for _, _, filename in ROUTES:
        assert (FIXTURES_DIR / filename).is_file(), filename


@pytest.mark.parametrize(
    ("url", "fixture"),
    [
        ("https://dev.to/api/articles?tag=java&top=7", "devto_top.json"),
        ("https://huggingface.co/api/daily_papers", "hf_papers.json"),
        ("https://programming.dev/api/v3/post/list?sort=TopDay", "lemmy.json"),
        ("https://viblo.asia/api/posts/trending", "viblo_trending.json"),
        ("https://www.producthunt.com/feed", "producthunt.xml"),
        ("https://api.github.com/search/repositories?q=created:>2026-11-08", "github_search.json"),
    ],
)
def test_content_store_endpoints_are_served_offline(url: str, fixture: str) -> None:
    with httpx.Client(transport=offline_transport()) as client:
        response = client.get(url)

    assert response.status_code == 200
    assert response.content == (FIXTURES_DIR / fixture).read_bytes()
