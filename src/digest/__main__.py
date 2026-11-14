"""CLI entry point.

- `python -m digest run [--dry-run] [--only ...] [--date ...]`: the daily Telegram digest.
- `python -m digest ingest [--allow-remote]`: one content-store run (needs DIGEST_DB_URL).
- `python -m digest health [--dry-run] [--allow-remote]`: Telegram warning for failing sources.
"""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import psycopg
from dotenv import load_dotenv

from digest.ai.fake import fake_chain
from digest.ai.llm import AIUnavailable, LLMChain
from digest.ai.scorer import score_items
from digest.ai.summarizer import gather_context, summarize_items
from digest.config import Config, ConfigError, load_config
from digest.deliver.pages import build_pages
from digest.deliver.telegram import DeliveryError, send_messages
from digest.fetchers import fetch_all
from digest.filter import apply_filters, rank
from digest.health import render_health, stale_sources
from digest.http import make_client
from digest.ingest import IngestStats, run_ingest
from digest.logs import setup_logging
from digest.models import Digest, Item, ScoredItem
from digest.normalize import normalize
from digest.offline import is_offline, offline_transport
from digest.render import render_telegram, render_warning
from digest.state import (
    StateError,
    load_seen,
    save_digest,
    save_seen,
)
from digest.store import ContentStore, PostgresStore, SourceSpec, load_sources
from digest.store.dburl import DB_URL_ENV, DbUrlError, resolve_db_url

SOURCE_NAMES = ("hn", "github", "lobsters", "reddit", "rss")
TELEGRAM_ENV = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

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

    remote_help = "allow a non-local DIGEST_DB_URL outside GitHub Actions"
    ingest = commands.add_parser(
        "ingest", help="fetch due sources into the content store (DIGEST_DB_URL)"
    )
    ingest.add_argument("--config", type=Path, default=Path("config.yaml"), help="path to config")
    ingest.add_argument(
        "--sources", type=Path, default=Path("sources.yaml"), help="path to sources.yaml"
    )
    ingest.add_argument("--allow-remote", action="store_true", help=remote_help)

    health = commands.add_parser(
        "health", help="Telegram warning for sources failing > 24 h (DIGEST_DB_URL)"
    )
    health.add_argument("--config", type=Path, default=Path("config.yaml"), help="path to config")
    health.add_argument("--dry-run", action="store_true", help="print the warning; send nothing")
    health.add_argument("--allow-remote", action="store_true", help=remote_help)

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


async def _deliver(cfg: Config, messages: list[str], secrets: dict[str, str] | None) -> None:
    if is_offline():
        # Offline stub: print instead of sending, and report success so state is written.
        print("\n\n".join(messages))
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


def _make_chain(cfg: Config) -> LLMChain | None:
    """Real providers normally; the deterministic fake when offline; nothing when AI is off."""
    if not cfg.ai.enabled:
        return None
    return fake_chain(cfg.ai) if is_offline() else LLMChain.from_config(cfg.ai)


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
    stats = dict(digest.stats)
    for c in completions:
        stats["ai_tokens_in"] = int(stats.get("ai_tokens_in", 0)) + c.prompt_tokens
        stats["ai_tokens_out"] = int(stats.get("ai_tokens_out", 0)) + c.completion_tokens
    stats["summarized"] = sum(1 for i in items if i.summary)
    return digest.model_copy(update={"items": items, "stats": stats})


def _open_store(url: str) -> AbstractContextManager[ContentStore]:
    return PostgresStore.connect(url)


def _connect(url: str) -> AbstractContextManager[ContentStore] | None:
    """Open the store; on failure print why without the URL (libpq messages name the
    host and user) and return None."""
    try:
        return _open_store(url)
    except psycopg.Error as exc:
        print(f"could not connect to the database ({type(exc).__name__})", file=sys.stderr)
        return None


async def _run_ingest(store: ContentStore, cfg: Config, specs: list[SourceSpec]) -> IngestStats:
    offline = is_offline()
    async with make_client(offline_transport() if offline else None) as client:
        return await run_ingest(
            store,
            cfg,
            specs,
            client,
            now=datetime.now(UTC),
            github_token=os.environ.get("GITHUB_TOKEN") or None,
            devto_pause_seconds=0 if offline else 1.0,
        )


def ingest_main(args: argparse.Namespace, db_env: Mapping[str, str]) -> int:
    try:
        cfg = load_config(args.config)
        specs = load_sources(args.sources).specs()  # a bad file fails before connecting
        url = resolve_db_url(db_env, allow_remote=args.allow_remote)
    except (ConfigError, DbUrlError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if url is None:
        print(f"{DB_URL_ENV} is not set; nowhere to ingest into", file=sys.stderr)
        return 2
    store = _connect(url)
    if store is None:
        return 1
    with store as opened:
        stats = asyncio.run(_run_ingest(opened, cfg, specs))
    log.info("%s", stats.line())
    print(stats.line())
    return 0


def health_main(args: argparse.Namespace, db_env: Mapping[str, str]) -> int:
    try:
        cfg = load_config(args.config)
        url = resolve_db_url(db_env, allow_remote=args.allow_remote)
    except (ConfigError, DbUrlError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if url is None:
        log.info("health: %s is not set, skipping", DB_URL_ENV)
        return 0
    store = _connect(url)
    if store is None:
        return 1
    with store as opened:
        sources = opened.list_sources(enabled_only=True)
    now = datetime.now(cfg.tz)
    stale = stale_sources(sources, now)
    print(f"health: {len(stale)} of {len(sources)} enabled sources failing for over 24 h")
    if not stale:
        return 0
    message = render_health(stale, now, cfg.ai.output_language)
    if args.dry_run:
        print(message)
        return 0
    try:
        secrets = _telegram_secrets(cfg, dry_run=False)
        asyncio.run(_deliver(cfg, [message], secrets))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    except DeliveryError as exc:
        log.error("telegram delivery failed: %s", exc)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()
    # The DB URL is read before .env is loaded: .env holds the production URL under
    # another name, and ingest must only ever use an explicitly exported DIGEST_DB_URL.
    db_env = {k: os.environ[k] for k in (DB_URL_ENV, "GITHUB_ACTIONS") if k in os.environ}
    load_dotenv(Path(".env"))
    if args.command == "ingest":
        return ingest_main(args, db_env)
    if args.command == "health":
        return health_main(args, db_env)

    try:
        cfg = load_config(args.config)
        seen = load_seen(args.data_dir / "seen.json")
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

    chain = _make_chain(cfg)
    digest = build_digest(cfg, asyncio.run(_fetch(cfg, now, args.only)), seen, run_date, chain)
    if chain is not None:
        digest = add_summaries(cfg, digest, chain, offline_transport() if is_offline() else None)
    log.info("stats %s", " ".join(f"{k}={v}" for k, v in digest.stats.items()))

    language = cfg.ai.output_language
    all_failed = digest.stats["fetched"] == 0
    if all_failed:
        log.error("every source returned 0 items; sending a warning instead of a digest")
        messages = [render_warning(run_date, language)]
    else:
        messages = render_telegram(digest, language)

    if args.dry_run:
        print("\n\n".join(messages))
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
    if cfg.delivery.pages.enabled:
        build_pages(cfg, args.data_dir / "digests", args.site_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
