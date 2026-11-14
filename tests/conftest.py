import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from digest.config import Config

FIXTURES = Path(__file__).parent / "fixtures"

# Real credentials must never reach a test, even if exported in the developer's shell.
SECRET_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "GH_READ_TOKEN",
    "GITHUB_TOKEN",
)

# Shortly after the fixtures were downloaded (2026-09-27 ~17:43 UTC).
NOW = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def cfg() -> Config:
    return Config.model_validate({"profile": "Backend developer."})


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never touch the real network: the CLI serves fixtures instead."""
    monkeypatch.setenv("DIGEST_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _isolated_cwd(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative paths (data/, site/, .env) resolve inside a temp dir, never the repo."""
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    for var in SECRET_ENV:
        monkeypatch.delenv(var, raising=False)


# Integration tests run against a local Supabase Postgres (`supabase status`, port 54322).
TEST_DB_ENV = "DIGEST_TEST_DB_URL"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_CONTENT_TABLES = "public.item_tags, public.tags, public.items, public.clusters, public.sources"


@pytest.fixture
def pg_conn() -> Iterator[psycopg.Connection]:
    """A connection inside one transaction that is rolled back after the test.

    Skipped without DIGEST_TEST_DB_URL, except in CI (CI=true), where a missing database
    is a failure. Only local hosts are accepted, so tests can never touch production.
    """
    url = os.environ.get(TEST_DB_ENV)
    if not url:
        if os.environ.get("CI"):
            pytest.fail(f"{TEST_DB_ENV} is not set; CI must run the Postgres integration tests")
        pytest.skip(f"{TEST_DB_ENV} not set (local Supabase Postgres)")
    if conninfo_to_dict(url).get("host") not in _LOCAL_HOSTS:
        pytest.fail(f"{TEST_DB_ENV} must point at a local database")
    conn = psycopg.connect(url, prepare_threshold=None)
    try:
        # Starts the outer transaction: store calls become savepoints inside it, and the
        # truncate (transactional in Postgres) is undone together with the test's writes.
        conn.execute(f"truncate {_CONTENT_TABLES} cascade")
        yield conn
    finally:
        conn.rollback()
        conn.close()
