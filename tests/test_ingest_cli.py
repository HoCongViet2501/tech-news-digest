"""`python -m digest ingest` CLI: DB URL safety and a fixture run through the CLI."""

import logging
from contextlib import nullcontext
from pathlib import Path

import psycopg
import pytest

from digest.__main__ import main
from digest.store import MemoryStore, PostgresStore, load_sources
from digest.store.dburl import DbUrlError, is_local, resolve_db_url

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "s3cret-pw"
REMOTE = f"postgresql://postgres.abcref:{SECRET}@aws-0-region.pooler.supabase.com:5432/postgres"
LOCAL = f"postgresql://postgres:{SECRET}@127.0.0.1:54322/postgres"


def args(*extra: str) -> list[str]:
    return [
        "ingest",
        "--config",
        str(REPO_ROOT / "config.yaml"),
        "--sources",
        str(REPO_ROOT / "sources.yaml"),
        "--taxonomy",
        str(REPO_ROOT / "taxonomy.yaml"),
        *extra,
    ]


def assert_no_secret(capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture) -> str:
    out = capsys.readouterr()
    for text in (out.out, out.err, caplog.text):
        for secret in (SECRET, "pooler.supabase.com", "abcref"):
            assert secret not in text
    return out.out + out.err


@pytest.fixture(autouse=True)
def _outside_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replaces the Postgres connection with a MemoryStore; records connect calls."""
    calls: list[str] = []

    def fake(url: str):  # type: ignore[no-untyped-def]
        calls.append(url)
        return nullcontext(MemoryStore())

    monkeypatch.setattr("digest.__main__._open_store", fake)
    return calls


# --- URL rules ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("postgresql://postgres:pw@127.0.0.1:54322/postgres", True),
        ("postgresql://postgres:pw@localhost/postgres", True),
        ("postgresql://postgres@[::1]:5432/postgres", True),
        ("host=/var/run/postgresql dbname=postgres", True),
        (REMOTE, False),
        ("postgresql://postgres@127.0.0.1,db.example.com/postgres", False),
        ("postgresql:///postgres", False),  # no host: libpq would fall back to PGHOST
        ("host=localhost hostaddr=10.0.0.5 dbname=postgres", False),
        ("service=prod", False),
    ],
)
def test_is_local(url: str, local: bool) -> None:
    assert is_local(url) is local


def test_resolve_db_url_rules() -> None:
    assert resolve_db_url({}, allow_remote=False) is None
    assert resolve_db_url({"DIGEST_DB_URL": LOCAL}, allow_remote=False) == LOCAL
    with pytest.raises(DbUrlError) as exc:
        resolve_db_url({"DIGEST_DB_URL": REMOTE}, allow_remote=False)
    assert SECRET not in str(exc.value) and "pooler" not in str(exc.value)
    assert resolve_db_url({"DIGEST_DB_URL": REMOTE}, allow_remote=True) == REMOTE
    env = {"DIGEST_DB_URL": REMOTE, "GITHUB_ACTIONS": "true"}
    assert resolve_db_url(env, allow_remote=False) == REMOTE
    with pytest.raises(DbUrlError) as exc:
        resolve_db_url(
            {"DIGEST_DB_URL": f"postgresql://x:{SECRET}@h:5432/db?bogus=1"}, allow_remote=True
        )
    assert SECRET not in str(exc.value)


# --- CLI -----------------------------------------------------------------------


def test_remote_url_is_refused_outside_actions(
    monkeypatch: pytest.MonkeyPatch,
    opened: list[str],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DIGEST_DB_URL", REMOTE)

    assert main(args()) == 2

    assert opened == []
    assert "--allow-remote" in assert_no_secret(capsys, caplog)


@pytest.mark.parametrize("how", ["flag", "actions"])
def test_remote_url_is_allowed_with_flag_or_in_actions(
    monkeypatch: pytest.MonkeyPatch,
    opened: list[str],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    how: str,
) -> None:
    monkeypatch.setenv("DIGEST_DB_URL", REMOTE)
    if how == "actions":
        monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with caplog.at_level(logging.DEBUG):
        assert main(args("--allow-remote") if how == "flag" else args()) == 0

    assert opened == [REMOTE]
    assert "ingest due=" in assert_no_secret(capsys, caplog)


def test_db_url_in_dotenv_is_ignored(
    opened: list[str], capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    # cwd is a temp dir (conftest); main() loads ./.env.
    Path(".env").write_text(f"DIGEST_DB_URL={LOCAL}\nSUPABASE_DB_URL={REMOTE}\n")

    assert main(args()) == 2

    assert opened == []
    assert "DIGEST_DB_URL is not set" in assert_no_secret(capsys, caplog)


def test_connection_error_does_not_leak_the_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def refuse(url: str) -> None:
        raise psycopg.OperationalError(f"connection to {url} failed: password for abcref wrong")

    monkeypatch.setattr("digest.__main__._open_store", refuse)
    monkeypatch.setenv("DIGEST_DB_URL", REMOTE)

    assert main(args("--allow-remote")) == 1

    assert "OperationalError" in assert_no_secret(capsys, caplog)


@pytest.mark.integration
def test_cli_ingest_into_local_postgres(
    monkeypatch: pytest.MonkeyPatch,
    pg_conn: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DIGEST_OFFLINE=1 run of the real sources.yaml through the CLI into Postgres."""
    store = PostgresStore(pg_conn)
    monkeypatch.setattr("digest.__main__._open_store", lambda url: nullcontext(store))
    monkeypatch.setenv("DIGEST_DB_URL", LOCAL)

    with caplog.at_level(logging.DEBUG):
        assert main(args()) == 0
        assert main(args()) == 0

    first, second = assert_no_secret(capsys, caplog).splitlines()
    assert "new=0" not in first
    assert "due=0 fetched=0 new=0" in second  # nothing due yet: no duplicates, no refetch
    specs = load_sources(REPO_ROOT / "sources.yaml").specs()
    assert len(store.list_sources(enabled_only=True)) == len(specs)
