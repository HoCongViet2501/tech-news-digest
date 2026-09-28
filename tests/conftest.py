from datetime import UTC, datetime
from pathlib import Path

import pytest

from digest.config import Config

FIXTURES = Path(__file__).parent / "fixtures"

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
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
