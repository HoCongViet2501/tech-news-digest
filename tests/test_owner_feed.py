"""The Telegram digest from the owner's My Feed (docs/PLAN.md §16 W4 step 6), and its
fallback to the Part 1 digest."""

# F811: the imported `world` fixture is requested as a parameter of the same name.
# ruff: noqa: F811

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from test_personalization import World, as_role, make_user, world  # noqa: F401

from digest.__main__ import main, owner_digest
from digest.ai.fake import fake_chain
from digest.config import Config, load_config
from digest.models import Digest
from digest.owner_feed import FEED_MAX, card_to_item, owner_items
from digest.render import render_telegram
from digest.store import FeedCard, PostgresStore
from digest.store.models import FeedMention
from snapshot import assert_snapshot

REPO_CONFIG = Path(__file__).parent.parent / "config.yaml"
LOCAL_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
OWNER = uuid.UUID("00000000-0000-4000-8000-000000000001")
PUBLISHED = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def card(n: int, **kw: Any) -> FeedCard:
    data: dict[str, Any] = {
        "id": f"{n:040x}",
        "title": f"Story {n}",
        "url": f"https://example.com/{n}",
        "discussion_url": f"https://news.ycombinator.com/item?id={n}",
        "published_at": PUBLISHED,
        "source_id": 1,
        "source_name": "Hacker News",
        "upvotes": 100 + n,
        "comments": n,
        "mentions": [{"source_id": 1, "source_name": "Hacker News"}],
        "tldr": f"TL;DR số {n}.",
    }
    return FeedCard.model_validate(data | kw)


class FakeOwnerStore:
    def __init__(
        self, cards: list[FeedCard], *, user: uuid.UUID | None = OWNER, follows: bool = True
    ) -> None:
        self.cards, self.user, self.follows = cards, user, follows
        self.lookups: list[tuple[str, str]] = []

    def find_user(self, *, github_login: str, email: str) -> uuid.UUID | None:
        self.lookups.append((github_login, email))
        return self.user

    def follows_tags(self, user_id: uuid.UUID) -> bool:
        assert user_id == self.user
        return self.follows

    def feed_for_user(self, user_id: uuid.UUID, n: int) -> list[FeedCard]:
        assert (user_id, n) == (self.user, FEED_MAX)
        return self.cards[:n]


def owner_cfg(**extra: Any) -> Config:
    data = {"profile": "x", "delivery": {"telegram": {"owner_github_login": "Octo"}}}
    return Config.model_validate(data | extra)


# --- owner_items -----------------------------------------------------------------


def test_top_unsent_items_in_feed_order() -> None:
    store = FakeOwnerStore([card(n) for n in range(1, 16)])
    seen = {f"{2:040x}": "2026-11-14", f"{5:040x}": "2026-11-14"}

    found = owner_items(store, owner_cfg(max_items=10), seen)

    assert found is not None
    items, candidates = found
    assert candidates == 15
    assert [i.title for i in items] == [f"Story {n}" for n in (1, 3, 4, 6, 7, 8, 9, 10, 11, 12)]
    assert store.lookups == [("Octo", "")]


@pytest.mark.parametrize(
    "store",
    [
        FakeOwnerStore([card(1)], user=None),
        FakeOwnerStore([card(1)], follows=False),
        FakeOwnerStore([]),
        FakeOwnerStore([card(1)]),  # only an item that was already sent
    ],
    ids=["no-account", "no-followed-tags", "empty-feed", "nothing-new"],
)
def test_falls_back_to_part_1(store: FakeOwnerStore) -> None:
    assert owner_items(store, owner_cfg(), {f"{1:040x}": "2026-11-14"}) is None


def test_card_to_item_uses_tldr_and_lists_other_sites() -> None:
    mentions = [
        FeedMention(source_id=1, source_name="Hacker News"),
        FeedMention(source_id=7, source_name="Lobsters"),
        FeedMention(source_id=9, source_name="Lobsters"),
    ]
    item = card_to_item(card(3, mentions=mentions, tldr="  Ngắn gọn.  "))

    assert item.summary == "Ngắn gọn."
    assert item.also_on == ["Lobsters"]
    assert (item.source, item.score, item.comments) == ("Hacker News", 103, 3)
    assert item.reason is None and item.why_it_matters is None
    assert card_to_item(card(4, tldr="  ")).summary is None


# --- rendering -------------------------------------------------------------------


def my_feed_digest() -> Digest:
    items = [
        card_to_item(card(1, title="Kafka 4.2 & <KRaft> 🚀", url="https://example.com/a?x=1&y=2")),
        card_to_item(
            card(
                2,
                discussion_url=None,
                source_name="Lobsters",
                mentions=[
                    {"source_id": 1, "source_name": "Lobsters"},
                    {"source_id": 2, "source_name": "Hacker News"},
                ],
            )
        ),
        card_to_item(card(3, tldr=None)),
    ]
    return Digest(date=date(2026, 11, 15), items=items, stats={"feed": "owner"})


def test_telegram_links_titles_to_the_web_and_keeps_the_original() -> None:
    [msg] = render_telegram(my_feed_digest(), "vi", "https://digest.example.app/")

    assert f'1. <a href="https://digest.example.app/items/{1:040x}">Kafka 4.2 &amp; ' in msg
    assert '· <a href="https://example.com/a?x=1&amp;y=2">bài gốc</a>' in msg
    assert "TL;DR số 1." in msg
    assert "cũng có trên Hacker News" in msg
    assert_snapshot("telegram_my_feed_vi.html", msg)


def test_without_web_base_url_titles_link_to_the_article() -> None:
    [msg] = render_telegram(my_feed_digest(), "vi", "")

    assert '1. <a href="https://example.com/a?x=1&amp;y=2">' in msg
    assert "/items/" not in msg and "bài gốc" not in msg


# --- owner_digest and the CLI ------------------------------------------------------


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    raw = yaml.safe_load(REPO_CONFIG.read_text(encoding="utf-8"))
    raw["web"] = {"base_url": "https://digest.example.app"}
    raw["delivery"]["telegram"]["owner_github_login"] = "Octo"
    for key, value in overrides.items():
        raw[key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def run_cli(cfg_path: Path, data: Path, *extra: str) -> int:
    args = ["run", "--config", str(cfg_path), "--data-dir", str(data)]
    return main([*args, "--site-dir", str(data / "site"), *extra])


@pytest.fixture
def no_part1(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args: object) -> None:
        raise AssertionError("the Part 1 sources must not be fetched")

    monkeypatch.setattr("digest.__main__._fetch", fail)


def use_store(monkeypatch: pytest.MonkeyPatch, store: object) -> list[str]:
    opened: list[str] = []

    def opener(url: str) -> Any:
        opened.append(url)
        return nullcontext(store)

    monkeypatch.setenv("DIGEST_DB_URL", LOCAL_URL)
    monkeypatch.setattr("digest.__main__._open_owner_store", opener)
    return opened


def test_cli_sends_the_owner_feed_and_records_it_as_seen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_part1: None,
) -> None:
    cards = [card(n) for n in range(1, 13)]
    cards[0] = card(1, url="https://lwn.net/Articles/1000/", tldr=None)  # offline article
    opened = use_store(monkeypatch, FakeOwnerStore(cards))
    data = tmp_path / "data"

    assert run_cli(write_config(tmp_path), data) == 0

    out = capsys.readouterr().out
    assert opened == [LOCAL_URL]
    assert f'1. <a href="https://digest.example.app/items/{1:040x}">Story 1</a>' in out
    assert "[offline] Tóm tắt giả lập cho “Story 1”" in out  # no TL;DR: LLM summary
    assert "TL;DR số 2." in out
    assert "11. <a" not in out
    seen = json.loads((data / "seen.json").read_text(encoding="utf-8"))
    assert set(seen) == {f"{n:040x}" for n in range(1, 11)}
    saved = json.loads(next((data / "digests").glob("*.json")).read_text(encoding="utf-8"))
    assert saved["stats"]["feed"] == "owner" and saved["stats"]["summarized"] == 1

    # The next day's run skips what was sent.
    assert run_cli(write_config(tmp_path), data, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Story 11" in out and "Story 12" in out and "Story 3<" not in out


def part1_output(tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> str:
    assert run_cli(write_config(tmp_path), tmp_path / name, "--dry-run") == 0
    return capsys.readouterr().out


def test_db_error_falls_back_to_part_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    expected = part1_output(tmp_path, capsys, "a")

    def refuse(url: str) -> Any:
        raise psycopg.OperationalError(f"connection to {url} failed")

    monkeypatch.setenv("DIGEST_DB_URL", LOCAL_URL)
    monkeypatch.setattr("digest.__main__._open_owner_store", refuse)

    assert part1_output(tmp_path, capsys, "b") == expected
    assert "my feed: database error (OperationalError); sending the Part 1 digest" in caplog.text
    assert "postgres:postgres" not in caplog.text


def test_owner_without_account_falls_back_to_part_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = part1_output(tmp_path, capsys, "a")
    use_store(monkeypatch, FakeOwnerStore([card(1)], user=None))

    assert part1_output(tmp_path, capsys, "b") == expected


@pytest.mark.parametrize("extra", [["--date", "2026-11-14"], ["--only", "hn"]])
def test_reruns_with_date_or_only_use_part_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    opened = use_store(monkeypatch, FakeOwnerStore([card(1)]))

    assert run_cli(write_config(tmp_path), tmp_path / "data", "--dry-run", *extra) == 0
    assert opened == []


def test_no_connection_without_owner_or_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    opened = use_store(monkeypatch, FakeOwnerStore([card(1)]))
    kw: dict[str, Any] = {"seen": {}, "run_date": date(2026, 11, 15), "chain": None}
    env = {"DIGEST_DB_URL": LOCAL_URL}

    no_owner = Config.model_validate({"profile": "x"})
    assert owner_digest(no_owner, env, allow_remote=False, **kw) is None
    assert owner_digest(owner_cfg(), {}, allow_remote=False, **kw) is None
    remote = {"DIGEST_DB_URL": "postgresql://u:secret@db.example.com/postgres"}
    assert owner_digest(owner_cfg(), remote, allow_remote=False, **kw) is None
    assert opened == []
    assert "secret" not in caplog.text and "non-local" in caplog.text

    digest = owner_digest(owner_cfg(), env, allow_remote=False, **kw)
    assert digest is not None and opened == [LOCAL_URL]
    assert digest.stats == {"feed": "owner", "candidates": 1, "sent": 1, "tldr": 1}


def test_ai_disabled_keeps_items_without_tldr_unsummarized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_store(monkeypatch, FakeOwnerStore([card(1, url="https://lwn.net/Articles/1/", tldr=None)]))
    cfg = owner_cfg(ai={"enabled": False})
    chain = fake_chain(cfg.ai)

    digest = owner_digest(
        cfg,
        {"DIGEST_DB_URL": LOCAL_URL},
        allow_remote=False,
        seen={},
        run_date=date(2026, 11, 15),
        chain=chain,
    )

    assert digest is not None and digest.items[0].summary is None


# --- Postgres: owner lookup and the real feed_for_user --------------------------------


def add_identity(conn: psycopg.Connection, user: str, login: str) -> None:
    conn.execute(
        "insert into auth.identities (provider_id, user_id, identity_data, provider) "
        "values (%s, %s, %s, 'github')",
        (str(uuid.uuid4().int % 10**8), user, json.dumps({"user_name": login, "sub": "1"})),
    )


def confirm(conn: psycopg.Connection, user: str) -> None:
    conn.execute("update auth.users set email_confirmed_at = now() where id = %s", (user,))


@pytest.mark.integration
def test_find_user_by_github_login_or_confirmed_email(pg_conn: psycopg.Connection) -> None:
    store = PostgresStore(pg_conn)
    octo = make_user(pg_conn, "octo@users.noreply.github.com", {"user_name": "Octo"})
    add_identity(pg_conn, octo, "Octo")
    owner = make_user(pg_conn, "Owner@Example.com")
    confirm(pg_conn, owner)
    unconfirmed = make_user(pg_conn, "new@example.com")
    # Claims the login in client-controlled metadata only: no GitHub identity.
    make_user(pg_conn, "fake@example.com", {"user_name": "mallory"})

    assert store.find_user(github_login="octo", email="") == uuid.UUID(octo)
    assert store.find_user(github_login="", email="owner@example.COM") == uuid.UUID(owner)
    assert store.find_user(github_login="nobody", email="owner@example.com") == uuid.UUID(owner)
    assert store.find_user(github_login="", email="new@example.com") is None
    assert store.find_user(github_login="mallory", email="") is None
    assert store.find_user(github_login="", email="") is None
    # Two different accounts match: ambiguous, so none.
    assert store.find_user(github_login="octo", email="owner@example.com") is None
    pg_conn.execute("update auth.users set deleted_at = now() where id = %s", (owner,))
    assert store.find_user(github_login="", email="owner@example.com") is None
    assert unconfirmed


@contextmanager
def owner_in_world(world: World, tags: list[str]) -> Iterator[str]:
    conn = world.conn
    user = make_user(conn, "owner@example.com", {"user_name": "Octo"})
    add_identity(conn, user, "Octo")
    if tags:
        with as_role(conn, "authenticated", user):
            conn.execute("select public.set_followed_tags(%s)", (tags,))
    yield user


@pytest.mark.integration
def test_cli_digest_from_the_real_feed_for_user(
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_part1: None,
) -> None:
    conn = world.conn
    kafka = [i for i, t in world.items.items() if t == "kafka"]
    conn.execute(
        "update public.items set tldr = 'Tóm tắt Kafka: ' || title where id = any(%s)", (kafka,)
    )
    with owner_in_world(world, ["kafka", "databases", "go"]) as user:
        store = PostgresStore(conn)
        expected = [c.id for c in store.feed_for_user(uuid.UUID(user), 10)]
        use_store(monkeypatch, store)
        cfg_path = write_config(tmp_path, ai={"enabled": False})

        assert run_cli(cfg_path, tmp_path / "data", "--dry-run") == 0

    out = capsys.readouterr().out
    links = [f'. <a href="https://digest.example.app/items/{i}">' for i in expected]
    positions = [out.index(link) for link in links]
    assert positions == sorted(positions)
    assert "Tóm tắt Kafka: kafka story" in out
    assert out.count("bài gốc") == 10
    assert user not in out


@pytest.mark.integration
def test_owner_without_followed_tags_gets_part_1(world: World) -> None:
    with owner_in_world(world, []) as user:
        store = PostgresStore(world.conn)
        assert store.follows_tags(uuid.UUID(user)) is False
        assert owner_items(store, owner_cfg(), {}) is None


@pytest.mark.integration
def test_feed_for_user_cards_carry_the_tldr(world: World) -> None:
    conn = world.conn
    with owner_in_world(world, ["kafka", "databases", "go"]) as user:
        store = PostgresStore(conn)
        first = store.feed_for_user(uuid.UUID(user), 5)[0]
        conn.execute("update public.items set tldr = 'X' where id = %s", (first.id,))
        cards = store.feed_for_user(uuid.UUID(user), 60)

    assert len(cards) == 60
    assert cards[0].id == first.id and cards[0].tldr == "X"
    assert cards[0].source_name.startswith("Blog ")
    assert cards[0].published_at > datetime.now(UTC) - timedelta(days=4)


def test_repo_config_names_an_owner_but_leaves_the_web_url_empty() -> None:
    cfg = load_config(REPO_CONFIG)
    assert cfg.delivery.telegram.owner_github_login
    assert cfg.web.base_url == ""
