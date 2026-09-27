from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from digest.__main__ import main, parse_args, resolve_run_date

REPO_CONFIG = Path(__file__).parent.parent / "config.yaml"


def test_run_defaults() -> None:
    args = parse_args(["run"])

    assert args.command == "run"
    assert args.dry_run is False
    assert args.only is None
    assert args.date is None
    assert args.config == Path("config.yaml")


def test_run_with_all_options() -> None:
    args = parse_args(["run", "--dry-run", "--only", "hn,lobsters", "--date", "2026-09-01"])

    assert args.dry_run is True
    assert args.only == ["hn", "lobsters"]
    assert args.date == date(2026, 9, 1)


def test_only_rejects_unknown_source(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["run", "--only", "hn,twitter"])

    assert exc.value.code == 2
    assert "twitter" in capsys.readouterr().err


def test_date_rejects_bad_format() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["run", "--date", "01/09/2026"])

    assert exc.value.code == 2


def test_command_is_required() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args([])

    assert exc.value.code == 2


def test_explicit_date_wins() -> None:
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    assert resolve_run_date(date(2026, 1, 2), tz, now=datetime(2026, 9, 1, tzinfo=UTC)) == date(
        2026, 1, 2
    )


def test_default_date_uses_configured_timezone() -> None:
    # 00:00 UTC cron fires at 07:00 in Vietnam; 20:00 UTC is already the next day there.
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    now = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)

    assert resolve_run_date(None, tz, now=now) == date(2026, 9, 2)


def test_main_succeeds_with_valid_config() -> None:
    code = main(["run", "--dry-run", "--date", "2026-09-01", "--config", str(REPO_CONFIG)])

    assert code == 0


def test_main_reports_config_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("max_items: 10\n", encoding="utf-8")

    code = main(["run", "--dry-run", "--config", str(bad)])

    assert code == 2
    assert "profile" in capsys.readouterr().err
