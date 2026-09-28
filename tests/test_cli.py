import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from digest.__main__ import main, parse_args, resolve_now, resolve_run_date

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


def test_now_is_real_clock_without_explicit_date() -> None:
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    real = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)

    assert resolve_now(None, tz, real) == real


def test_now_is_0700_local_on_explicit_date() -> None:
    tz = ZoneInfo("Asia/Ho_Chi_Minh")

    now = resolve_now(date(2026, 8, 15), tz, datetime(2026, 9, 1, tzinfo=UTC))

    # 07:00 in Vietnam (UTC+7) = 00:00 UTC, the time the daily cron fires
    assert now == datetime(2026, 8, 15, 0, 0, tzinfo=UTC)


def test_offline_dry_run_reports_items_per_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="digest")
    code = main(
        [
            "run",
            "--dry-run",
            "--only",
            "hn,lobsters",
            "--date",
            "2026-09-27",
            "--config",
            str(REPO_CONFIG),
            "--data-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "fetched_hn=47" in caplog.text
    assert "fetched_lobsters=25" in caplog.text
    # 47 + 25 minus 5 URLs present on both (checked against the fixtures by hand)
    assert "after_dedupe=67" in caplog.text
    # thresholds + include/exclude keywords from config.yaml, counted by hand
    assert "after_filter=33" in caplog.text
    assert captured.out.startswith("<b>Bản tin công nghệ · 2026-09-27</b>")
    numbered = [line for line in captured.out.splitlines() if re.match(r"\d+\. <a ", line)]
    assert len(numbered) == 10


def test_main_reports_corrupt_state(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "seen.json").write_text("{broken", encoding="utf-8")

    code = main(["run", "--dry-run", "--config", str(REPO_CONFIG), "--data-dir", str(tmp_path)])

    assert code == 2
    assert "seen.json" in capsys.readouterr().err


def run_offline(data: Path, site: Path, *extra: str) -> int:
    return main(
        [
            "run",
            "--date",
            "2026-09-27",
            "--config",
            str(REPO_CONFIG),
            "--data-dir",
            str(data),
            "--site-dir",
            str(site),
            *extra,
        ]
    )


def test_offline_run_delivers_via_stub_and_saves_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, site = tmp_path / "data", tmp_path / "site"

    assert run_offline(data, site) == 0

    out = capsys.readouterr().out
    assert out.startswith("<b>Bản tin công nghệ · 2026-09-27</b>")
    seen = json.loads((data / "seen.json").read_text(encoding="utf-8"))
    digest = json.loads((data / "digests" / "2026-09-27.json").read_text(encoding="utf-8"))
    assert len(digest["items"]) == 10
    assert set(seen) == {i["id"] for i in digest["items"]}
    assert set(seen.values()) == {"2026-09-27"}
    assert (site / "index.html").is_file()
    assert (site / "2026-09-27.html").is_file()
    assert (site / "feed.xml").is_file()


def test_second_run_does_not_repeat_items(tmp_path: Path) -> None:
    data, site = tmp_path / "data", tmp_path / "site"
    digest_file = data / "digests" / "2026-09-27.json"

    assert run_offline(data, site) == 0
    first = [i["id"] for i in json.loads(digest_file.read_text(encoding="utf-8"))["items"]]
    assert run_offline(data, site) == 0
    both = [i["id"] for i in json.loads(digest_file.read_text(encoding="utf-8"))["items"]]

    second = both[len(first) :]
    assert both[: len(first)] == first
    assert len(second) == 10
    assert not set(first) & set(second)


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    data, site = tmp_path / "data", tmp_path / "site"

    assert run_offline(data, site, "--dry-run") == 0

    assert not data.exists()
    assert not site.exists()


def test_missing_telegram_secrets_fail_before_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DIGEST_OFFLINE")

    def no_network(*args, **kwargs):
        raise AssertionError("must not fetch without secrets")

    monkeypatch.setattr("digest.__main__._fetch", no_network)

    code = run_offline(tmp_path / "data", tmp_path / "site")

    assert code == 2
    err = capsys.readouterr().err
    assert "TELEGRAM_BOT_TOKEN" in err
    assert "TELEGRAM_CHAT_ID" in err


def test_telegram_failure_exits_nonzero_and_saves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from digest.deliver.telegram import DeliveryError

    async def failing(*args, **kwargs):
        raise DeliveryError("HTTP 400: can't parse entities")

    monkeypatch.setattr("digest.__main__._deliver", failing)
    data = tmp_path / "data"

    assert run_offline(data, tmp_path / "site") == 1
    assert not (data / "seen.json").exists()
    assert not (data / "digests").exists()


def test_all_sources_failing_sends_warning_instead_of_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def nothing(cfg, now, only):
        return {"hn": [], "github": [], "lobsters": [], "rss": []}

    monkeypatch.setattr("digest.__main__._fetch", nothing)
    data = tmp_path / "data"

    assert run_offline(data, tmp_path / "site") == 0
    out = capsys.readouterr().out
    assert "⚠️" in out
    assert "<a " not in out
    assert not (data / "digests").exists()
