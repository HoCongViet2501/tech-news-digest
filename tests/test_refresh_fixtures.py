from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from refresh_fixtures import Target, build_targets, refresh

A = Target(name="a", url="https://a.example/feed.json", filename="a.json")
B = Target(name="b", url="https://b.example/page.html", filename="b.html")


@respx.mock
def test_successful_responses_are_written(tmp_path: Path) -> None:
    respx.get(A.url).respond(200, content=b'{"ok": 1}')
    respx.get(B.url).respond(200, content=b"<html></html>")

    with httpx.Client() as client:
        failures = refresh(client, [A, B], tmp_path)

    assert failures == []
    assert (tmp_path / "a.json").read_bytes() == b'{"ok": 1}'
    assert (tmp_path / "b.html").read_bytes() == b"<html></html>"


@respx.mock
def test_error_status_keeps_old_fixture_and_continues(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_bytes(b"old")
    respx.get(A.url).respond(403)
    respx.get(B.url).respond(200, content=b"new")

    with httpx.Client() as client:
        failures = refresh(client, [A, B], tmp_path)

    assert failures == ["a"]
    assert (tmp_path / "a.json").read_bytes() == b"old"
    assert (tmp_path / "b.html").read_bytes() == b"new"


@respx.mock
def test_network_error_is_reported_not_raised(tmp_path: Path) -> None:
    respx.get(A.url).mock(side_effect=httpx.ConnectError("boom"))

    with httpx.Client() as client:
        failures = refresh(client, [A], tmp_path)

    assert failures == ["a"]
    assert not (tmp_path / "a.json").exists()


def test_hn_target_asks_for_last_24h_only() -> None:
    now = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)  # epoch 1788220800

    hn = next(t for t in build_targets(now) if t.name == "hackernews")

    params = httpx.URL(hn.url).params
    assert "created_at_i>1788134400" in params["numericFilters"].split(",")


def test_every_target_has_a_unique_filename() -> None:
    targets = build_targets(datetime(2026, 9, 1, tzinfo=UTC))

    filenames = [t.filename for t in targets]
    assert len(filenames) == len(set(filenames))
