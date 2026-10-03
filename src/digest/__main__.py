"""CLI entry point: `python -m digest run [--dry-run] [--only ...] [--date ...]`."""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from digest.ai.llm import AIUnavailable, Completion, LLMChain
from digest.ai.release_notes import summarize_releases
from digest.ai.scorer import score_items
from digest.ai.summarizer import gather_context, summarize_items
from digest.config import Config, ConfigError, load_config
from digest.deliver.pages import build_pages
from digest.deliver.telegram import DeliveryError, send_messages
from digest.fetchers import fetch_all
from digest.filter import apply_filters, rank
from digest.http import make_client
from digest.logs import setup_logging
from digest.models import Digest, Item, ScoredItem, TelegramMessage
from digest.normalize import normalize
from digest.offline import is_offline, offline_transport
from digest.radar.github import github_token
from digest.radar.run import RadarResult, run_radar
from digest.render import render_warning, telegram_messages
from digest.state import (
    StateError,
    load_radar_map,
    load_seen,
    save_digest,
    save_radar_map,
    save_seen,
)

SOURCE_NAMES = ("hn", "github", "lobsters", "reddit", "rss")
TELEGRAM_ENV = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
RADAR_STATE = "radar_state.json"  # reported radar ids -> date, same format as seen.json
RADAR_MAP = "radar_map.json"

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
    run.add_argument("--site-dir", type=Path, default=Path("site"), help="Pages output directory")

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


async def _deliver(
    cfg: Config, messages: list[TelegramMessage], secrets: dict[str, str] | None
) -> None:
    if is_offline():
        # Offline stub: print instead of sending, and report success so state is written.
        print("\n\n".join(m.text for m in messages))
        return
    if not cfg.delivery.telegram.enabled or secrets is None:
        log.info("telegram: disabled, not sending")
        return
    async with make_client() as client:
        await send_messages(
            client, secrets["TELEGRAM_BOT_TOKEN"], secrets["TELEGRAM_CHAT_ID"], messages
        )


def _telegram_secrets(cfg: Config, dry_run: bool) -> dict[str, str] | None:
    """Env secrets needed for a real send; raises ConfigError naming any missing variable."""
    if dry_run or is_offline() or not cfg.delivery.telegram.enabled:
        return None
    missing = [name for name in TELEGRAM_ENV if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"Missing environment variable(s): {', '.join(missing)}")
    return {name: os.environ[name] for name in TELEGRAM_ENV}


def build_digest(
    cfg: Config,
    fetched: dict[str, list[Item]],
    seen: dict[str, str],
    run_date: date,
    chain: LLMChain | None = None,
) -> Digest:
    stats: dict[str, int | str] = {f"fetched_{name}": len(items) for name, items in fetched.items()}
    items = [item for batch in fetched.values() for item in batch]
    stats["fetched"] = len(items)
    items = normalize(items)
    stats["after_dedupe"] = len(items)
    items = apply_filters(items, cfg, seen)
    stats["after_filter"] = len(items)
    if not cfg.ai.enabled:
        ranked = rank(items, cfg, limit=cfg.max_items)
    else:
        # Widen the heuristic cut so the AI has enough candidates to choose from.
        candidates = rank(items, cfg, limit=cfg.ai.candidates)
        try:
            ranked, completion = score_items(chain or LLMChain([]), cfg, candidates)
        except AIUnavailable:
            log.warning("ai: unavailable, sending heuristic ranking")
            ranked = candidates[: cfg.max_items]
            stats["ai"] = "unavailable"
        else:
            stats["ai"] = completion.provider
            stats["ai_tokens_in"] = completion.prompt_tokens
            stats["ai_tokens_out"] = completion.completion_tokens
    stats["sent"] = len(ranked)
    return Digest(date=run_date, items=ranked, stats=stats)


async def _contexts(
    cfg: Config, items: list[ScoredItem], transport: httpx.AsyncBaseTransport | None
) -> dict[str, str]:
    async with make_client(transport) as client:
        texts = await asyncio.gather(
            *(gather_context(client, item, cfg.ai.article_max_chars) for item in items)
        )
    return {item.id: text for item, text in zip(items, texts, strict=True) if text}


def add_summaries(
    cfg: Config,
    digest: Digest,
    chain: LLMChain,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Digest:
    """Summarize the final items; skipped when AI scoring was unavailable."""
    if not cfg.ai.enabled or digest.stats.get("ai") in (None, "unavailable"):
        return digest
    contexts = asyncio.run(_contexts(cfg, digest.items, transport))
    items, completions = summarize_items(chain, cfg, digest.items, contexts)
    stats = _add_tokens(digest.stats, completions)
    stats["summarized"] = sum(1 for i in items if i.summary)
    return digest.model_copy(update={"items": items, "stats": stats})


def _add_tokens(
    stats: dict[str, int | str], completions: Sequence[Completion]
) -> dict[str, int | str]:
    stats = dict(stats)
    for c in completions:
        stats["ai_tokens_in"] = int(stats.get("ai_tokens_in", 0)) + c.prompt_tokens
        stats["ai_tokens_out"] = int(stats.get("ai_tokens_out", 0)) + c.completion_tokens
    return stats


async def _radar(
    cfg: Config, now: datetime, reported: dict[str, str], repo_map: dict[str, str | None]
) -> RadarResult:
    transport = offline_transport() if is_offline() else None
    token = None if is_offline() else github_token()
    async with make_client(transport) as client:
        return await run_radar(cfg.radar, client, now, reported, repo_map, token)


def add_radar(
    cfg: Config, digest: Digest, result: RadarResult, chain: LLMChain | None = None
) -> Digest:
    """Attach radar entries; release notes are condensed by AI when it worked today."""
    entries, completions = result.entries, []
    if chain is not None and digest.stats.get("ai") not in (None, "unavailable"):
        entries, completions = summarize_releases(chain, cfg, entries)
    stats = _add_tokens(digest.stats, completions)
    stats["radar_dependencies"] = result.dependencies
    stats["radar"] = len(entries)
    return digest.model_copy(update={"radar": entries, "stats": stats})


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()
    load_dotenv(Path(".env"))

    try:
        cfg = load_config(args.config)
        seen = load_seen(args.data_dir / "seen.json")
        reported = load_seen(args.data_dir / RADAR_STATE) if cfg.radar.enabled else {}
        secrets = _telegram_secrets(cfg, args.dry_run)
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

    chain = LLMChain.from_config(cfg.ai) if cfg.ai.enabled else None
    digest = build_digest(cfg, asyncio.run(_fetch(cfg, now, args.only)), seen, run_date, chain)
    if chain is not None:
        digest = add_summaries(cfg, digest, chain, offline_transport() if is_offline() else None)
    radar = None
    if cfg.radar.enabled:
        repo_map = load_radar_map(args.data_dir / RADAR_MAP)
        radar = asyncio.run(_radar(cfg, now, reported, repo_map))
        digest = add_radar(cfg, digest, radar, chain)
    log.info("stats %s", " ".join(f"{k}={v}" for k, v in digest.stats.items()))

    language = cfg.ai.output_language
    all_failed = digest.stats["fetched"] == 0
    if all_failed:
        log.error("every source returned 0 items; sending a warning instead of a digest")
        messages = [TelegramMessage(text=render_warning(run_date, language))]
    else:
        messages = telegram_messages(digest, language, buttons=cfg.feedback.enabled)

    if args.dry_run:
        print("\n\n".join(m.text for m in messages))
        return 0

    try:
        asyncio.run(_deliver(cfg, messages, secrets))
    except DeliveryError as exc:
        log.error("telegram delivery failed, state not saved: %s", exc)
        return 1
    if all_failed:
        return 0

    # State is written only after delivery succeeded.
    save_seen(
        args.data_dir / "seen.json",
        seen,
        [item.id for item in digest.items],
        run_date,
        cfg.filters.seen_retention_days,
    )
    save_digest(args.data_dir / "digests", digest)
    if radar is not None:
        save_seen(
            args.data_dir / RADAR_STATE,
            reported,
            [entry.id for entry in digest.radar],
            run_date,
            cfg.radar.state_retention_days,
        )
        save_radar_map(args.data_dir / RADAR_MAP, radar.repo_map)
    if cfg.delivery.pages.enabled:
        build_pages(cfg, args.data_dir / "digests", args.site_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
