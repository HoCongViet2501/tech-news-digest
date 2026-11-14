from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import respx

from digest.__main__ import main
from digest.health import render_health, stale_sources
from digest.render import TELEGRAM_LIMIT
from digest.store import FetchOutcome, MemoryStore, Source, SourceSpec

NOW = datetime(2026, 11, 15, 0, 5, tzinfo=UTC)
TELEGRAM = "https://api.telegram.org/bot123:abc/sendMessage"


def source(name: str, **kw: object) -> Source:
    data: dict[str, object] = {
        "id": abs(hash(name)) % 10_000,
        "kind": "rss",
        "name": name,
        "url": f"https://{name}.example/feed",
        "created_at": NOW - timedelta(days=30),
    }
    return Source.model_validate(data | kw)


def test_stale_sources() -> None:
    day = timedelta(hours=24)
    sources = [
        source("fresh", last_ok_at=NOW - timedelta(hours=2)),
        # Failing, last success more than 24 h ago -> stale.
        source("failing", last_ok_at=NOW - day - timedelta(hours=1), last_error="HTTP 500"),
        # Failing but succeeded within 24 h -> not yet.
        source("blip", last_ok_at=NOW - timedelta(hours=20), last_error="timeout"),
        # Daily feed fetched fine 24 h and 5 min ago, just waiting for its next run.
        source("waiting", last_ok_at=NOW - day - timedelta(minutes=5)),
        # No error, but nobody fetched it for longer than cadence + 24 h: ingest stopped.
        source("abandoned", cadence_hours=3, last_ok_at=NOW - timedelta(hours=28)),
        # Never succeeded, created long ago -> stale; created recently -> not yet.
        source("never", last_error="HTTP 404"),
        source("new", created_at=NOW - timedelta(hours=3)),
        source("disabled", enabled=False, last_error="HTTP 500"),
    ]

    stale = stale_sources(sources, NOW)

    assert [s.name for s in stale] == ["failing", "abandoned", "never"]


def test_render_health_escapes_and_fits_one_message() -> None:
    stale = [
        source(f"feed<{n}>", last_error="Server error '500' <html>" * 20, last_ok_at=NOW)
        for n in range(200)
    ]

    message = render_health(stale, NOW, "en")

    assert message.startswith("⚠️ <b>Sources failing for over 24 h · 2026-11-15</b>")
    assert "feed&lt;0&gt;" in message and "<html>" not in message
    assert len(message) <= TELEGRAM_LIMIT
    assert message.rstrip().split("\n")[-1].startswith("… +")
    assert "chưa bao giờ" in render_health([source("x")], NOW, "vi")


# --- CLI -------------------------------------------------------------------


@pytest.fixture
def memory(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    store = MemoryStore(clock=lambda: datetime.now(UTC) - timedelta(days=30))
    store.sync_sources(
        [
            SourceSpec(kind="rss", name="Good", url="https://good.example/feed"),
            SourceSpec(kind="rss", name="Dead", url="https://dead.example/feed"),
        ]
    )
    monkeypatch.setattr("digest.__main__._open_store", lambda url: nullcontext(store))
    monkeypatch.setenv("DIGEST_DB_URL", "postgresql://postgres:pw@127.0.0.1:54322/postgres")
    return store


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    monkeypatch.delenv("DIGEST_OFFLINE")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    config = tmp_path_factory.mktemp("cfg") / "config.yaml"
    config.write_text("profile: x\nai: {output_language: en}\n", encoding="utf-8")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(TELEGRAM).respond(200, json={"ok": True})
        yield route, str(config)


def test_health_sends_one_message_when_sources_are_stale(memory: MemoryStore, telegram) -> None:
    route, config = telegram
    now = datetime.now(UTC)
    good, dead = memory.list_sources()
    memory.record_fetch(good.id, FetchOutcome(at=now - timedelta(hours=1), ok=True))
    memory.record_fetch(dead.id, FetchOutcome(at=now - timedelta(hours=1), ok=False, error="503"))

    assert main(["health", "--config", config]) == 0

    assert route.call_count == 1
    text = route.calls.last.request.content.decode()
    assert "Dead" in text and "Good" not in text


def test_health_is_silent_when_all_sources_are_fine(memory: MemoryStore, telegram) -> None:
    route, config = telegram
    for s in memory.list_sources():
        memory.record_fetch(s.id, FetchOutcome(at=datetime.now(UTC), ok=True))

    assert main(["health", "--config", config]) == 0

    assert not route.called


def test_health_without_db_url_skips_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_store(url: str) -> None:
        raise AssertionError("must not connect")

    monkeypatch.setattr("digest.__main__._open_store", no_store)
    Path("config.yaml").write_text("profile: x\n", encoding="utf-8")  # cwd is a temp dir

    assert main(["health"]) == 0

    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
