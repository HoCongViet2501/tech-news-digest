"""CLI entry point: `python -m digest run [--dry-run] [--only ...] [--date ...]`."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from digest.config import Config, ConfigError, load_config
from digest.fetchers import fetch_all
from digest.filter import apply_filters, rank
from digest.http import make_client
from digest.models import Digest, Item
from digest.normalize import normalize
from digest.offline import is_offline, offline_transport
from digest.render import render_telegram
from digest.state import StateError, load_seen

SOURCE_NAMES = ("hn", "github", "lobsters", "reddit", "rss")

log = logging.getLogger("digest")


def _parse_only(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    unknown = [name for name in names if name not in SOURCE_NAMES]
    if unknown or not names:
        bad = ", ".join(unknown) or repr(value)
        raise argparse.ArgumentTypeError(
            f"unknown source(s) {bad}; choose from {', '.join(SOURCE_NAMES)}"
        )
    return names


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="digest", description="Daily tech news digest.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="collect, filter, render and deliver the digest")
    run.add_argument(
        "--dry-run", action="store_true", help="print the digest; send nothing, write no state"
    )
    run.add_argument(
        "--only", type=_parse_only, help=f"comma-separated subset of: {', '.join(SOURCE_NAMES)}"
    )
    run.add_argument("--date", type=_parse_date, help="run for a specific day (YYYY-MM-DD)")
    run.add_argument("--config", type=Path, default=Path("config.yaml"), help="path to config")
    run.add_argument("--data-dir", type=Path, default=Path("data"), help="state directory")

    return parser.parse_args(argv)


def resolve_run_date(explicit: date | None, tz: ZoneInfo, now: datetime | None = None) -> date:
    if explicit is not None:
        return explicit
    return (now or datetime.now(tz)).astimezone(tz).date()


def resolve_now(explicit: date | None, tz: ZoneInfo, real_now: datetime) -> datetime:
    """Reference time for fetch windows: real clock, or 07:00 local on a re-run date."""
    if explicit is None:
        return real_now
    return datetime.combine(explicit, time(7, 0), tzinfo=tz)


async def _fetch(cfg: Config, now: datetime, only: list[str] | None) -> dict[str, list[Item]]:
    transport = offline_transport() if is_offline() else None
    async with make_client(transport) as client:
        return await fetch_all(cfg, client, now, only)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()

    try:
        cfg = load_config(args.config)
        seen = load_seen(args.data_dir / "seen.json")
    except (ConfigError, StateError) as exc:
        print(exc, file=sys.stderr)
        return 2

    real_now = datetime.now(cfg.tz)
    run_date = resolve_run_date(args.date, cfg.tz, real_now)
    now = resolve_now(args.date, cfg.tz, real_now)
    log.info(
        "run date=%s dry_run=%s offline=%s only=%s",
        run_date,
        args.dry_run,
        is_offline(),
        ",".join(args.only) if args.only else "all",
    )

    fetched = asyncio.run(_fetch(cfg, now, args.only))
    stats: dict[str, int | str] = {f"fetched_{name}": len(items) for name, items in fetched.items()}
    items = [item for batch in fetched.values() for item in batch]
    stats["fetched"] = len(items)
    items = normalize(items)
    stats["after_dedupe"] = len(items)
    items = apply_filters(items, cfg, seen)
    stats["after_filter"] = len(items)
    digest = Digest(date=run_date, items=rank(items, cfg, limit=cfg.max_items), stats=stats)
    digest.stats["sent"] = len(digest.items)
    log.info("stats %s", " ".join(f"{k}={v}" for k, v in digest.stats.items()))

    messages = render_telegram(digest, cfg.ai.output_language)
    if args.dry_run:
        print("\n\n".join(messages))
        return 0
    # Delivery and state saving are added in step 7.
    return 0


if __name__ == "__main__":
    sys.exit(main())
