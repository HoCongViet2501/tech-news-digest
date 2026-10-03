import asyncio
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from conftest import fixture_bytes
from digest.__main__ import main
from digest.feedback.collect import (
    FeedbackError,
    get_updates,
    items_by_prefix,
    next_offset,
    parse_votes,
)
from digest.http import make_client
from digest.models import Digest, ScoredItem
from digest.state import latest_votes, load_votes, save_digest

TOKEN = "123456:SECRET-token-value"
URL = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
NOW = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)
REPO_CONFIG = Path(__file__).parents[2] / "config.yaml"


def item(prefix: str, title: str, source: str = "hn") -> ScoredItem:
    return ScoredItem(
        id=prefix + "0" * 32,
        source=source,
        title=title,
        url=f"https://example.com/{prefix}",
        score=100,
        published_at=NOW,
        relevance=5.0,
    )


ITEMS = items_by_prefix(
    [item("aaaaaaaa", "Postgres 19 released"), item("bbbbbbbb", "Crypto drama")]
)


def updates() -> list[dict]:
    return json.loads(fixture_bytes("telegram_getUpdates.json"))["result"]


def fetch(offset: int | None):
    async def go():
        async with make_client() as client:
            return await get_updates(client, TOKEN, offset)

    return asyncio.run(go())


def test_parse_votes_keeps_taps_from_our_chat_on_known_items() -> None:
    votes = parse_votes(updates(), "42", ITEMS, set(), NOW)

    assert [(v.update_id, v.item_id[:8], v.vote) for v in votes] == [
        (500000001, "aaaaaaaa", "up"),
        (500000002, "bbbbbbbb", "down"),
        (500000004, "aaaaaaaa", "down"),
    ]
    assert votes[0].title == "Postgres 19 released"
    assert votes[0].source == "hn"


def test_latest_vote_wins() -> None:
    latest = latest_votes(parse_votes(updates(), "42", ITEMS, set(), NOW))

    assert latest["aaaaaaaa" + "0" * 32].vote == "down"
    assert latest["bbbbbbbb" + "0" * 32].vote == "down"


def test_known_updates_are_skipped() -> None:
    votes = parse_votes(updates(), "42", ITEMS, {500000001, 500000002}, NOW)

    assert [v.update_id for v in votes] == [500000004]


def test_next_offset_confirms_everything_seen() -> None:
    assert next_offset(updates(), None) == 500000007
    assert next_offset([], 500000007) == 500000007


@respx.mock
def test_get_updates_sends_offset_and_only_asks_for_callbacks() -> None:
    route = respx.get(URL).respond(200, content=fixture_bytes("telegram_getUpdates.json"))

    assert len(fetch(500000001)) == 6

    params = route.calls.last.request.url.params
    assert params["offset"] == "500000001"
    assert json.loads(params["allowed_updates"]) == ["callback_query"]


@respx.mock
def test_errors_do_not_leak_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    respx.get(URL).respond(409, json={"ok": False, "description": "Conflict: webhook is active"})

    with pytest.raises(FeedbackError, match="webhook is active") as exc:
        fetch(None)
    assert TOKEN not in str(exc.value)

    respx.get(URL).mock(side_effect=httpx.ConnectError(f"cannot reach {URL}"))
    with pytest.raises(FeedbackError) as exc:
        fetch(None)
    assert TOKEN not in str(exc.value)
    assert TOKEN not in caplog.text


# --- CLI (offline: getUpdates is served from the fixture) ----------------------


def seed(data: Path) -> None:
    items = [item("aaaaaaaa", "Postgres 19 released"), item("bbbbbbbb", "Crypto drama", "lobsters")]
    save_digest(data / "digests", Digest(date=date(2026, 9, 27), items=items))


def collect(data: Path, *extra: str) -> int:
    return main(
        ["feedback", "--config", str(feedback_config(data)), "--data-dir", str(data), *extra]
    )


def feedback_config(data: Path) -> Path:
    import yaml

    raw = yaml.safe_load(REPO_CONFIG.read_text(encoding="utf-8"))
    raw["feedback"] = {"enabled": True}
    path = data.parent / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_collect_appends_votes_and_saves_offset(tmp_path: Path) -> None:
    data = tmp_path / "data"
    seed(data)

    assert collect(data) == 0

    votes = load_votes(data / "feedback.jsonl")
    assert [(v.item_id[:8], v.vote) for v in votes] == [
        ("aaaaaaaa", "up"),
        ("bbbbbbbb", "down"),
        ("aaaaaaaa", "down"),
    ]
    assert json.loads((data / "telegram_offset.json").read_text()) == {"offset": 500000007}


def test_collecting_twice_does_not_duplicate(tmp_path: Path) -> None:
    # The offline fixture returns the same updates again, like Telegram would if the
    # previous offset never reached the repo; update ids keep the log clean.
    data = tmp_path / "data"
    seed(data)

    assert collect(data) == 0
    assert collect(data) == 0

    assert len(load_votes(data / "feedback.jsonl")) == 3


def test_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    seed(data)

    assert collect(data, "--dry-run") == 0

    assert "  up hn Postgres 19 released" in capsys.readouterr().out
    assert not (data / "feedback.jsonl").exists()
    assert not (data / "telegram_offset.json").exists()


def test_broken_feedback_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "feedback.jsonl"
    good = parse_votes(updates(), "42", ITEMS, set(), NOW)[0].model_dump_json()
    path.write_text("{broken\n" + good + "\n", encoding="utf-8")

    assert [v.update_id for v in load_votes(path)] == [500000001]


def test_real_run_requires_telegram_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DIGEST_OFFLINE")

    assert collect(tmp_path / "data") == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err
