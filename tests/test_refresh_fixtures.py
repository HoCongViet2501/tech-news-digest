import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from refresh_fixtures import Target, build_targets, refresh, trim_atom, trim_json

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


@respx.mock
def test_target_with_json_body_is_posted(tmp_path: Path) -> None:
    target = Target("osv", "https://osv.example/v1/querybatch", "sub/osv.json", {"queries": []})
    route = respx.post(target.url).respond(200, content=b'{"results": []}')

    with httpx.Client() as client:
        assert refresh(client, [target], tmp_path) == []

    assert route.calls.last.request.content == b'{"queries":[]}'
    assert (tmp_path / "sub/osv.json").read_bytes() == b'{"results": []}'


def test_trim_json_keeps_first_entries_and_drops_personal_fields() -> None:
    raw = json.dumps(
        {"posts": [{"post": {"id": i}, "creator": {"name": "x"}} for i in range(5)], "n": 1}
    ).encode()

    data = json.loads(trim_json(2, key="posts")(raw))

    assert data == {"posts": [{"post": {"id": 0}}, {"post": {"id": 1}}], "n": 1}


def test_trim_atom_keeps_first_entries_without_authors() -> None:
    entries = "".join(
        f"<entry><title>{i}</title><author><name>P{i}</name></author></entry>" for i in range(4)
    )
    raw = f"<feed><title>F</title>{entries}</feed>".encode()

    text = trim_atom(2)(raw).decode()

    assert text.count("<entry>") == 2
    assert "<title>1</title>" in text and "<title>2</title>" not in text
    assert "<author>" not in text
    assert text.endswith("</feed>")


@respx.mock
def test_trim_is_applied_before_writing(tmp_path: Path) -> None:
    target = Target("t", "https://t.example/x.json", "x.json", trim=trim_json(1))
    respx.get(target.url).respond(200, content=b'[{"id": 1}, {"id": 2}]')

    with httpx.Client() as client:
        assert refresh(client, [target], tmp_path) == []

    assert json.loads((tmp_path / "x.json").read_bytes()) == [{"id": 1}]
